"""
services/staged/circuit_breaker_health/contract.py

FastAPI contract for the circuit‑breaker health endpoint.
Mirrors the structure of ``services/_exemplar/contract.py``.
"""

from __future__ import annotations

import datetime as _dt
from typing import List

import fastapi
import pydantic
import sqlalchemy as _sa
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import Table, Column, DateTime, String, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# --------------------------------------------------------------------------- #
# Real application data‑layer imports (required by the “no‑hollow” rule)
# --------------------------------------------------------------------------- #
from app.db import get_session  # pragma: no cover

# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class DaemonStatus(pydantic.BaseModel):
    name: str
    status: str
    age_seconds: int
    last_heartbeat: str


class HealthResponse(pydantic.BaseModel):
    breaker_state: str
    checked_at: str
    daemons: List[DaemonStatus]


# --------------------------------------------------------------------------- #
# Service‑health thresholds (seconds)
# --------------------------------------------------------------------------- #
_THRESHOLDS: dict[str, int] = {
    "write_service": 300,
    "self_diagnostics": 600,
    "rug_pull_monitor": 28_800,
    # default for any other daemon
    "*": 60,
}


def _classify(name: str, age: int) -> str:
    """Return OK | STALE | UNKNOWN for a daemon."""
    if age < 0:
        return "UNKNOWN"
    limit = _THRESHOLDS.get(name, _THRESHOLDS.get("*", 60))
    return "STALE" if age > limit else "OK"


# --------------------------------------------------------------------------- #
# Core logic – queries the ``service_health`` table via the injected session
# --------------------------------------------------------------------------- #
def get_circuit_breaker_health(db: Session) -> HealthResponse:
    """Collect health information for all daemons."""
    # ``service_health`` is not part of ``app.models``; we use a lightweight Table.
    metadata = _sa.MetaData()
    service_health = Table(
        "service_health",
        metadata,
        Column("name", String, primary_key=True),
        Column("last_heartbeat", DateTime, nullable=False),
    )

    stmt = select(service_health.c.name, service_health.c.last_heartbeat)
    rows = db.execute(stmt).all()

    now = _dt.datetime.utcnow()
    daemons: List[DaemonStatus] = []
    stale_cnt = 0

    for name, last_hb in rows:
        age = int((now - last_hb).total_seconds())
        status = _classify(name, age)
        if status == "STALE":
            stale_cnt += 1
        daemons.append(
            DaemonStatus(
                name=name,
                status=status,
                age_seconds=age,
                last_heartbeat=last_hb.isoformat() + "Z",
            )
        )

    breaker_state = "OK" if stale_cnt == 0 else "STALLED"

    return HealthResponse(
        breaker_state=breaker_state,
        checked_at=now.isoformat() + "Z",
        daemons=daemons,
    )


# --------------------------------------------------------------------------- #
# FastAPI router
# --------------------------------------------------------------------------- #
router = fastapi.APIRouter()


@router.get(
    "/api/circuit-breaker/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
)
def health_endpoint(db: Session = Depends(get_session)):
    try:
        return get_circuit_breaker_health(db)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Application instance (used by the test client)
# --------------------------------------------------------------------------- #
app = FastAPI()
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test (run with ``python -m services.staged.circuit_breaker_health.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite engine and create the ``service_health`` table
    # ------------------------------------------------------------------- #
    engine: Engine = _sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = _sa.MetaData()
    service_health = Table(
        "service_health",
        metadata,
        Column("name", String, primary_key=True),
        Column("last_heartbeat", DateTime, nullable=False),
    )
    metadata.create_all(engine)

    # ------------------------------------------------------------------- #
    # Helper to provide a session bound to the in‑memory engine
    # ------------------------------------------------------------------- #
    TestSession = sessionmaker(bind=engine)

    def get_test_session() -> Session:  # pragma: no cover
        with TestSession() as sess:
            yield sess

    # ------------------------------------------------------------------- #
    # Seed five daemon rows – a mix of fresh and stale timestamps
    # ------------------------------------------------------------------- #
    now = _dt.datetime.utcnow()
    seed = [
        # name, seconds ago (age)
        ("write_service", 100),          # OK (threshold 300)
        ("self_diagnostics", 700),       # STALE (threshold 600)
        ("rug_pull_monitor", 30_000),    # STALE (threshold 28_800)
        ("unknown_daemon", 30),          # OK (default 60)
        ("another_daemon", 120),         # STALE (default 60)
    ]

    with TestSession() as sess:
        for name, age_sec in seed:
            hb = now - _dt.timedelta(seconds=age_sec)
            sess.execute(
                service_health.insert().values(name=name, last_heartbeat=hb)
            )
        sess.commit()

    # ------------------------------------------------------------------- #
    # Override the real ``get_session`` dependency with the test version
    # ------------------------------------------------------------------- #
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Run the test client against the endpoint
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/api/circuit-breaker/health")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # Verify that the number of stale daemons matches our seed
    stale_expected = sum(1 for _, age in seed if age > _THRESHOLDS.get(_, _THRESHOLDS["*"]))
    stale_reported = sum(1 for d in data["daemons"] if d["status"] == "STALE")
    assert stale_reported == stale_expected, (
        f"Stale count mismatch: expected {stale_expected}, got {stale_reported}"
    )

    print("PASS")