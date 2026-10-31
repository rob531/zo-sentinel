import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from sqlalchemy.orm import Session

# Real data layer imports (must not be mocked here)
from app.db import get_session, Base
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry-staleness"])


class ServerInfo(BaseModel):
    server_id: str
    name: str
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None
    age_days: int
    band: str
    last_assessed: Optional[datetime.datetime] = None
    last_seen: Optional[datetime.datetime] = None


class BandCount(BaseModel):
    band: str
    count: int


class RegistryStalenessResponse(BaseModel):
    days_threshold: int = Field(..., description="Staleness threshold in days")
    total_stale: int = Field(..., description="Number of stale servers")
    by_band: List[BandCount]
    servers: List[ServerInfo]


def _bucket(age: int) -> str:
    if age <= 7:
        return "0-7"
    if age <= 30:
        return "8-30"
    if age <= 90:
        return "31-90"
    return "90+"


@router.get(
    "/registry-staleness",
    response_model=RegistryStalenessResponse,
    summary="List stale registry servers",
)
def get_registry_staleness(
    days: int = Query(30, ge=0, description="Staleness threshold in days"),
    limit: int = Query(100, gt=0, description="Maximum number of servers to return"),
    risk_tier: Optional[str] = Query(
        None, description="Optional risk tier filter (exact match)"
    ),
    session: Session = Depends(get_session),
):
    now = datetime.datetime.utcnow()
    query = session.query(McpServerRegistry)

    if risk_tier:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)

    rows = query.limit(limit).all()

    stale_servers: List[ServerInfo] = []
    band_counter: dict[str, int] = {}

    for row in rows:
        ref_ts = row.last_assessed or row.last_seen
        if not ref_ts:
            continue
        age_days = (now - ref_ts).days
        if age_days <= days:
            continue

        band = _bucket(age_days)
        band_counter[band] = band_counter.get(band, 0) + 1

        stale_servers.append(
            ServerInfo(
                server_id=row.server_id,
                name=row.name,
                risk_tier=row.risk_tier,
                trust_score=row.trust_score,
                age_days=age_days,
                band=band,
                last_assessed=row.last_assessed,
                last_seen=row.last_seen,
            )
        )

    by_band = [BandCount(band=b, count=c) for b, c in sorted(band_counter.items())]

    return RegistryStalenessResponse(
        days_threshold=days,
        total_stale=len(stale_servers),
        by_band=by_band,
        servers=stale_servers,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.registry_last_assessed_staleness_api.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # Build an in‑memory SQLite app for the test
    TEST_DB_URL = "sqlite:///:memory:"
    engine = create_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=engine)

    # Create tables using the real Base metadata
    Base.metadata.create_all(bind=engine)

    # Populate test data
    now = datetime.datetime.utcnow()
    test_rows = [
        McpServerRegistry(
            server_id="s_fresh",
            name="Fresh Server",
            risk_tier="low",
            trust_score=0.9,
            last_assessed=now - datetime.timedelta(days=10),
            last_seen=now - datetime.timedelta(days=10),
        ),
        McpServerRegistry(
            server_id="s_40d",
            name="40‑Day Stale",
            risk_tier="medium",
            trust_score=0.7,
            last_assessed=now - datetime.timedelta(days=40),
            last_seen=now - datetime.timedelta(days=40),
        ),
        McpServerRegistry(
            server_id="s_200d",
            name="200‑Day Stale",
            risk_tier="high",
            trust_score=0.5,
            last_assessed=now - datetime.timedelta(days=200),
            last_seen=now - datetime.timedelta(days=200),
        ),
    ]

    with TestSessionLocal() as sess:
        sess.add_all(test_rows)
        sess.commit()

    # Override the dependency to use the test session
    def override_get_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    resp = client.get("/api/registry-staleness?days=30")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    if data["total_stale"] != 2:
        print(
            f"FAIL: expected total_stale 2, got {data['total_stale']}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Verify the 200‑day server is in the 90+ band
    server_200 = next(
        (s for s in data["servers"] if s["server_id"] == "s_200d"), None
    )
    if not server_200 or server_200["band"] != "90+":
        print(
            "FAIL: 200‑day server missing or not in 90+ band",
            file=sys.stderr,
        )
        sys.exit(1)

    # Verify the fresh server is excluded
    if any(s["server_id"] == "s_fresh" for s in data["servers"]):
        print("FAIL: fresh server should not be included", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)