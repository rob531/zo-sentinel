import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import Base, get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry_scan_freshness"])


class SourceStats(BaseModel):
    registry_source: str = Field(..., description="Source of the registry")
    total: int = Field(..., description="Total servers for this source")
    never_scanned: int = Field(..., description="Servers never scanned")
    scanned_24h: int = Field(..., description="Scanned within last 24h")
    scanned_7d: int = Field(..., description="Scanned >24h and <=7d")
    scanned_30d: int = Field(..., description="Scanned >7d and <=30d")
    stale_over_30d: int = Field(..., description="Scanned >30d ago")
    median_days_since_scan: Optional[float] = Field(
        None, description="Median days since last scan (null if no scans)"
    )


class RegistryScanFreshnessResponse(BaseModel):
    total_servers: int = Field(..., description="Total servers across all sources")
    never_scanned: int = Field(..., description="Total servers never scanned")
    sources: List[SourceStats] = Field(..., description="Per‑source statistics")


def _bucket_days(days: float) -> str:
    """Return bucket name for a given number of days since last scan."""
    if days <= 1:
        return "scanned_24h"
    if days <= 7:
        return "scanned_7d"
    if days <= 30:
        return "scanned_30d"
    return "stale_over_30d"


@router.get(
    "/registry/scan-freshness",
    response_model=RegistryScanFreshnessResponse,
    status_code=status.HTTP_200_OK,
)
def get_registry_scan_freshness(session: Session = Depends(get_session)):
    now = datetime.datetime.utcnow()
    stmt = select(McpServerRegistry)
    rows = session.execute(stmt).scalars().all()

    total_servers = len(rows)
    never_scanned_total = sum(1 for r in rows if r.last_scanned is None)

    # Group by registry_source
    source_map = {}
    for r in rows:
        src = r.registry_source or "unknown"
        src_entry = source_map.setdefault(
            src,
            {
                "total": 0,
                "never_scanned": 0,
                "scanned_24h": 0,
                "scanned_7d": 0,
                "scanned_30d": 0,
                "stale_over_30d": 0,
                "days": [],  # for median calculation
            },
        )
        src_entry["total"] += 1
        if r.last_scanned is None:
            src_entry["never_scanned"] += 1
        else:
            delta = now - r.last_scanned
            days = delta.total_seconds() / 86400.0
            src_entry["days"].append(days)
            bucket = _bucket_days(days)
            src_entry[bucket] += 1

    sources: List[SourceStats] = []
    for src, data in source_map.items():
        median: Optional[float] = None
        if data["days"]:
            sorted_days = sorted(data["days"])
            n = len(sorted_days)
            mid = n // 2
            if n % 2 == 1:
                median = sorted_days[mid]
            else:
                median = (sorted_days[mid - 1] + sorted_days[mid]) / 2.0
        sources.append(
            SourceStats(
                registry_source=src,
                total=data["total"],
                never_scanned=data["never_scanned"],
                scanned_24h=data["scanned_24h"],
                scanned_7d=data["scanned_7d"],
                scanned_30d=data["scanned_30d"],
                stale_over_30d=data["stale_over_30d"],
                median_days_since_scan=median,
            )
        )

    return RegistryScanFreshnessResponse(
        total_servers=total_servers,
        never_scanned=never_scanned_total,
        sources=sources,
    )


app = FastAPI()
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.registry_scan_freshness_api.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # Build an in‑memory SQLite engine compatible with the real Base metadata
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Dependency override to use the test session
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    now = datetime.datetime.utcnow()
    test_data = [
        # source A
        McpServerRegistry(
            server_id="srv-a-1",
            name="A1",
            registry_source="source_a",
            last_scanned=None,
            first_seen=now - datetime.timedelta(days=60),
            last_seen=now - datetime.timedelta(days=1),
            scan_count=0,
        ),
        McpServerRegistry(
            server_id="srv-a-2",
            name="A2",
            registry_source="source_a",
            last_scanned=now - datetime.timedelta(days=2),
            first_seen=now - datetime.timedelta(days=70),
            last_seen=now - datetime.timedelta(days=1),
            scan_count=1,
        ),
        McpServerRegistry(
            server_id="srv-a-3",
            name="A3",
            registry_source="source_a",
            last_scanned=now - datetime.timedelta(days=40),
            first_seen=now - datetime.timedelta(days=80),
            last_seen=now - datetime.timedelta(days=1),
            scan_count=1,
        ),
        # source B
        McpServerRegistry(
            server_id="srv-b-1",
            name="B1",
            registry_source="source_b",
            last_scanned=now - datetime.timedelta(hours=12),
            first_seen=now - datetime.timedelta(days=30),
            last_seen=now - datetime.timedelta(days=1),
            scan_count=1,
        ),
        McpServerRegistry(
            server_id="srv-b-2",
            name="B2",
            registry_source="source_b",
            last_scanned=now - datetime.timedelta(days=10),
            first_seen=now - datetime.timedelta(days=40),
            last_seen=now - datetime.timedelta(days=1),
            scan_count=1,
        ),
    ]

    with TestSession() as sess:
        sess.add_all(test_data)
        sess.commit()

    client = TestClient(app)
    resp = client.get("/api/registry/scan-freshness")
    if resp.status_code != 200:
        print(f"FAIL: Unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    payload = resp.json()
    # Overall never_scanned should be 1
    if payload.get("never_scanned") != 1:
        print(
            f"FAIL: never_scanned expected 1 got {payload.get('never_scanned')}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Find source_a stats
    source_a = next(
        (s for s in payload.get("sources", []) if s["registry_source"] == "source_a"),
        None,
    )
    if source_a is None:
        print("FAIL: source_a missing", file=sys.stderr)
        sys.exit(1)

    if source_a.get("stale_over_30d") != 1:
        print(
            f"FAIL: source_a stale_over_30d expected 1 got {source_a.get('stale_over_30d')}",
            file=sys.stderr,
        )
        sys.exit(1)

    print("PASS")
    sys.exit(0)