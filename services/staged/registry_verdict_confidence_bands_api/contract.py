# services/staged/registry_verdict_confidence_bands_api/contract.py
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Real data layer imports (must not be mocked here)
from app.db import Base, get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


# ---------- Pydantic response models ----------
class BandDetail(BaseModel):
    band: str
    count: int
    verdict_mix: Dict[str, int]


class TotalsResponse(BaseModel):
    servers: int
    scored: int
    null_confidence: int


class VerdictConfidenceBandsResponse(BaseModel):
    bands: List[BandDetail]
    totals: TotalsResponse


# ---------- Core logic ----------
_BAND_DEFS = [
    (0.0, 0.2),
    (0.2, 0.4),
    (0.4, 0.6),
    (0.6, 0.8),
    (0.8, 1.0),
]


def _band_label(low: float, high: float) -> str:
    return f"{low:.1f}-{high:.1f}"


@router.get(
    "/registry/verdict-confidence-bands",
    response_model=VerdictConfidenceBandsResponse,
    name="registry_verdict_confidence_bands",
)
def get_verdict_confidence_bands(
    registry_source: Optional[str] = Query(None, alias="registry_source"),
    limit: Optional[int] = Query(None, ge=1),
    db: Session = Depends(get_session),
):
    """
    Return confidence‑band statistics for servers in the registry.
    """
    query = db.query(McpServerRegistry)
    if registry_source:
        query = query.filter(McpServerRegistry.registry_source == registry_source)
    if limit:
        query = query.limit(limit)

    rows = query.all()

    total_servers = len(rows)
    scored = sum(1 for r in rows if r.confidence is not None)
    null_confidence = total_servers - scored

    # initialise band containers
    bands: Dict[str, Dict[str, object]] = {
        _band_label(low, high): {"count": 0, "verdict_mix": {}}
        for low, high in _BAND_DEFS
    }

    for r in rows:
        if r.confidence is None:
            continue
        for low, high in _BAND_DEFS:
            # include the upper bound only for the final band (1.0)
            if low <= r.confidence < high or (high == 1.0 and r.confidence == 1.0):
                label = _band_label(low, high)
                band_info = bands[label]
                band_info["count"] += 1
                vm: Dict[str, int] = band_info["verdict_mix"]
                vm[r.verdict] = vm.get(r.verdict, 0) + 1
                break

    band_list = [
        BandDetail(band=label, count=data["count"], verdict_mix=data["verdict_mix"])
        for label, data in bands.items()
    ]

    totals = TotalsResponse(
        servers=total_servers, scored=scored, null_confidence=null_confidence
    )
    return VerdictConfidenceBandsResponse(bands=band_list, totals=totals)


# ---------- Self‑test ----------
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # Build a minimal FastAPI app for the contract test
    test_app = FastAPI()
    test_app.include_router(router)

    # In‑memory SQLite engine (static pool for thread safety)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    # Dependency override to use the in‑memory session
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    with TestSession() as db:
        servers = [
            McpServerRegistry(
                server_id="s1",
                name="srv1",
                registry_source="srcA",
                verdict="TRUSTED_GENERAL",
                confidence=0.1,
                risk_tier="LOW",
                last_assessed=None,
                scan_count=0,
            ),
            McpServerRegistry(
                server_id="s2",
                name="srv2",
                registry_source="srcA",
                verdict="UNTRUSTED",
                confidence=0.35,
                risk_tier="MEDIUM",
                last_assessed=None,
                scan_count=0,
            ),
            McpServerRegistry(
                server_id="s3",
                name="srv3",
                registry_source="srcB",
                verdict="TRUSTED_GENERAL",
                confidence=0.5,
                risk_tier="MEDIUM",
                last_assessed=None,
                scan_count=0,
            ),
            McpServerRegistry(
                server_id="s4",
                name="srv4",
                registry_source="srcB",
                verdict="UNTRUSTED",
                confidence=0.9,
                risk_tier="HIGH",
                last_assessed=None,
                scan_count=0,
            ),
            McpServerRegistry(
                server_id="s5",
                name="srv5",
                registry_source="srcB",
                verdict="UNTRUSTED",
                confidence=None,
                risk_tier="LOW",
                last_assessed=None,
                scan_count=0,
            ),
        ]
        db.add_all(servers)
        db.commit()

    client = TestClient(test_app)

    resp = client.get("/api/registry/verdict-confidence-bands?limit=10")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    bands = data.get("bands", [])
    totals = data.get("totals", {})

    # Assertions per acceptance criteria
    assert len(bands) == 5, f"expected 5 bands, got {len(bands)}"
    band_08_10 = next((b for b in bands if b["band"] == "0.8-1.0"), None)
    assert band_08_10 is not None, "band 0.8-1.0 missing"
    assert band_08_10["count"] == 1, f"expected count 1 in 0.8-1.0 band, got {band_08_10['count']}"
    assert totals.get("null_confidence") == 1, f"expected null_confidence 1, got {totals.get('null_confidence')}"

    print("PASS")
    sys.exit(0)