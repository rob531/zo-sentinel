# services/staged/circuit_breaker_health_report/contract.py
"""
Circuit Breaker Health Report service contract.

Provides a GET endpoint at /api/health/circuit-breaker that returns the
current circuit‑breaker state, cohort trip history and per‑file quarantine
metadata.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

# --------------------------------------------------------------------------- #
# Data layer – use the real application session (overridden in the self‑test)
# --------------------------------------------------------------------------- #
from app.db import get_session  # noqa: F401  (imported for dependency injection)

# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class QuarantineEntry(BaseModel):
    file_path: str
    reason: str
    timestamp: Optional[str] = None


class CircuitBreakerHealth(BaseModel):
    tripped: bool
    cohort_trip_history: List[Any] = []
    quarantine: List[QuarantineEntry] = []


# --------------------------------------------------------------------------- #
# Helper – read the JSON files used by the gate orchestrator
# --------------------------------------------------------------------------- #
def _load_json(relative_path: str) -> Any:
    """Load a JSON file located under the repository root's ``app`` directory."""
    root = Path(__file__).resolve().parents[3]  # services/staged/... -> repo root
    file_path = root / "app" / relative_path
    if not file_path.is_file():
        return {}
    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_circuit_breaker_health(_: Session = Depends(get_session)) -> CircuitBreakerHealth:
    """
    Assemble the circuit‑breaker health report.

    The ``Session`` argument is kept for API‑compatibility with other contracts
    that require a DB session, even though this endpoint does not query the DB.
    """
    state = _load_json("gate_breaker_state.json")
    quarantine = _load_json("gate_quarantine.json")

    tripped: bool = bool(state.get("tripped", False))
    cohort_trip_history: List[Any] = state.get("cohort_trip_history", [])

    # ``quarantine`` is expected to be a list of dicts with at least
    # ``file_path`` and ``reason`` keys.
    quarantine_entries: List[QuarantineEntry] = []
    if isinstance(quarantine, list):
        for entry in quarantine:
            if isinstance(entry, dict):
                quarantine_entries.append(
                    QuarantineEntry(
                        file_path=entry.get("file_path", ""),
                        reason=entry.get("reason", ""),
                        timestamp=entry.get("timestamp"),
                    )
                )

    return CircuitBreakerHealth(
        tripped=tripped,
        cohort_trip_history=cohort_trip_history,
        quarantine=quarantine_entries,
    )


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api")


@router.get(
    "/health/circuit-breaker",
    response_model=CircuitBreakerHealth,
    tags=["health"],
    summary="Circuit‑breaker health report",
)
def circuit_breaker_health_report(
    health: CircuitBreakerHealth = Depends(get_circuit_breaker_health),
) -> CircuitBreakerHealth:
    """Return the current circuit‑breaker health payload."""
    return health


# --------------------------------------------------------------------------- #
# Self‑test (run with ``python -m services.staged.circuit_breaker_health_report.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Prepare an in‑memory SQLite session and override the real DB dependency
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=engine)

    async def _override_get_session() -> Session:  # pragma: no cover
        return TestSessionLocal()

    # ------------------------------------------------------------------- #
    # Seed the JSON files expected by the service
    # ------------------------------------------------------------------- #
    repo_root = Path(__file__).resolve().parents[3]
    app_dir = repo_root / "app"
    app_dir.mkdir(parents=True, exist_ok=True)

    (app_dir / "gate_breaker_state.json").write_text(
        json.dumps(
            {
                "tripped": True,
                "cohort_trip_history": [
                    {"time": "2023-01-01T00:00:00Z", "count": 5}
                ],
            }
        ),
        encoding="utf-8",
    )
    (app_dir / "gate_quarantine.json").write_text(
        json.dumps(
            [
                {
                    "file_path": "file1.txt",
                    "reason": "test reason 1",
                    "timestamp": "2023-01-02T00:00:00Z",
                },
                {
                    "file_path": "file2.txt",
                    "reason": "test reason 2",
                },
            ]
        ),
        encoding="utf-8",
    )

    # ------------------------------------------------------------------- #
    # Build FastAPI app for the test
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute the request and validate the response
    # ------------------------------------------------------------------- #
    resp = client.get("/api/health/circuit-breaker")
    assert resp.status_code == 200, f"unexpected status {resp.status_code}"
    payload = resp.json()
    assert payload.get("tripped") is True, "circuit breaker should be tripped"
    quarantine = payload.get("quarantine", [])
    assert isinstance(quarantine, list) and len(quarantine) >= 1, "quarantine list missing"
    assert any(entry.get("reason") for entry in quarantine), "no quarantine reason found"

    print("PASS")
    sys.exit(0)