# deps: fastapi, sqlalchemy, pydantic, requests
"""FastAPI router for daemon roster coverage API.

Reads from the service_health table (infrastructure table, queried via raw SQL
through the app db session). Each daemon has a known heartbeat-age threshold;
a daemon whose last heartbeat exceeds that threshold is "stale".
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db import get_session

router = APIRouter(prefix="/api")

# Per-daemon thresholds in seconds (derived from daemon configuration).
DAEMON_THRESHOLDS = {
    "sentinel_directive_generator": 7500,
    "self_diagnostics": 600,
    "write_service": 300,
    "rug_pull_monitor": 28800,
}
DEFAULT_THRESHOLD = 120.0


def _get_threshold(name: str) -> float:
    return DAEMON_THRESHOLDS.get(name, DEFAULT_THRESHOLD)


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class DaemonStatus(BaseModel):
    name: str = Field(..., description="Daemon identifier")
    status: str = Field(..., description="Current status")
    last_heartbeat: str = Field(..., description="ISO-8601 last heartbeat")
    age_seconds: float = Field(..., ge=0, description="Seconds since last heartbeat")
    is_stale: bool = Field(..., description="True when age exceeds threshold")
    threshold_seconds: float = Field(..., ge=0, description="Staleness threshold for this daemon")


class CoverageSummary(BaseModel):
    total: int = Field(..., ge=0, description="Total daemon count")
    healthy: int = Field(..., ge=0, description="Non-stale daemon count")
    stale: int = Field(..., ge=0, description="Stale daemon count")
    stale_names: List[str] = Field(default_factory=list, description="Stale daemon identifiers")


class CoverageResponse(BaseModel):
    daemons: List[DaemonStatus] = Field(default_factory=list)
    summary: CoverageSummary


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
@router.get(
    "/daemon/roster/coverage",
    response_model=CoverageResponse,
    tags=["daemon_roster_coverage_api"],
)
def daemon_roster_coverage(
    session: Session = Depends(get_session),
) -> CoverageResponse:
    """Return per-daemon heartbeat coverage and staleness summary.

    Queries the ``service_health`` table via the app db session using
    raw SQL (the table is an infrastructure table not in app/models.py).
    Staleness thresholds are per-daemon; unknown daemons use a 120 s
    default.
    """
    dialect = session.bind.dialect.name

    if dialect == "postgresql":
        now_expr = text("EXTRACT(EPOCH FROM NOW())")
        hb_expr = text("EXTRACT(EPOCH FROM last_heartbeat AT TIME ZONE 'UTC')")
    else:
        # SQLite / test in-memory
        now_expr = text("julianday('now') * 86400")
        hb_expr = text("julianday(last_heartbeat) * 86400")

    age_sql = f"({now_expr} - {hb_expr})"

    query = text(f"SELECT name, status, last_heartbeat, {age_sql} AS age_seconds FROM service_health")
    rows = session.execute(query).fetchall()

    daemons: List[DaemonStatus] = []
    stale_names: List[str] = []

    for row in rows:
        name: str = row[0]
        status: str = row[1]
        last_heartbeat = row[2]
        age_seconds = float(row[3])
        threshold = _get_threshold(name)
        is_stale = age_seconds > threshold

        if is_stale:
            stale_names.append(name)

        # Normalise heartbeat to ISO-8601 string for the response model
        hb_str = last_heartbeat.isoformat() if hasattr(last_heartbeat, "isoformat") else str(last_heartbeat)

        daemons.append(
            DaemonStatus(
                name=name,
                status=status,
                last_heartbeat=hb_str,
                age_seconds=round(age_seconds, 2),
                is_stale=is_stale,
                threshold_seconds=threshold,
            )
        )

    stale_count = len(stale_names)
    return CoverageResponse(
        daemons=daemons,
        summary=CoverageSummary(
            total=len(daemons),
            healthy=len(daemons) - stale_count,
            stale=stale_count,
            stale_names=stale_names,
        ),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import time
    from datetime import datetime, timedelta
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, Column, String, DateTime
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.ext.declarative import declarative_base

    Base = declarative_base()

    class MockServiceHealth(Base):
        __tablename__ = "service_health"
        name = Column(String, primary_key=True)
        status = Column(String)
        last_heartbeat = Column(DateTime)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    session: Session = TestingSessionLocal()
    now_ts = time.time()

    # Seed 4 mock daemons:
    #   write_service        threshold=300s   age=100s  → healthy
    #   self_diagnostics     threshold=600s   age=100s  → healthy
    #   sentinel_directive_generator threshold=7500s age=8000s → STALE
    #   rug_pull_monitor     threshold=28800s age=30000s → STALE
    session.add(MockServiceHealth(name="write_service", status="running",
                                  last_heartbeat=datetime.fromtimestamp(now_ts - 100)))
    session.add(MockServiceHealth(name="self_diagnostics", status="running",
                                  last_heartbeat=datetime.fromtimestamp(now_ts - 100)))
    session.add(MockServiceHealth(name="sentinel_directive_generator", status="running",
                                  last_heartbeat=datetime.fromtimestamp(now_ts - 8000)))
    session.add(MockServiceHealth(name="rug_pull_monitor", status="running",
                                  last_heartbeat=datetime.fromtimestamp(now_ts - 30000)))
    session.commit()
    session.close()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)
    resp = client.get("/api/daemon/roster/coverage")

    if resp.status_code != 200:
        print(f"FAIL: status {resp.status_code}")
        exit(1)

    data = resp.json()

    if data["summary"]["total"] != 4:
        print(f"FAIL: expected total=4, got {data['summary']['total']}")
        exit(1)
    if data["summary"]["stale"] != 2:
        print(f"FAIL: expected stale=2, got {data['summary']['stale']}")
        exit(1)

    stale_set = set(data["summary"]["stale_names"])
    expected_stale = {"sentinel_directive_generator", "rug_pull_monitor"}
    if stale_set != expected_stale:
        print(f"FAIL: expected stale={expected_stale}, got {stale_set}")
        exit(1)

    print("PASS")
