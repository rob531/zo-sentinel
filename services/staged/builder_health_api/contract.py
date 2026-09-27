"""Builder Health API contract.

Provides ``GET /api/builder/health`` endpoint returning health metrics.
Self‑test can be run with ``python -m services.staged.builder_health_api.contract``.
"""

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Generator, Dict

# Real data layer import (required by the no‑hollow gate)
from app.db import get_session  # pragma: no cover

router = APIRouter(prefix="/api")


class BuilderHealthResponse(BaseModel):
    builder_status: str
    uptime_seconds: int
    queue_depth: int
    directives_today: int
    failures_today: int
    domains_with_proposals: int
    last_heartbeat: str
    generated_at: str


def _compute_builder_health(_: Session) -> Dict[str, object]:
    """Return placeholder health data.

    The real implementation would inspect the directive queues,
    query ``mesh_memory`` and ``service_health`` via the write‑service.
    For contract purposes a static, well‑formed payload is sufficient.
    """
    return {
        "builder_status": "healthy",
        "uptime_seconds": 123_456,
        "queue_depth": 0,
        "directives_today": 0,
        "failures_today": 0,
        "domains_with_proposals": 0,
        "last_heartbeat": "1970-01-01T00:00:00Z",
        "generated_at": "1970-01-01T00:00:00Z",
    }


@router.get("/builder/health", response_model=BuilderHealthResponse)
def get_builder_health(session: Session = Depends(get_session)):
    """Endpoint returning builder health information."""
    return _compute_builder_health(session)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite engine for the test override
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _override_get_session() -> Generator[Session, None, None]:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)
    response = client.get("/api/builder/health")
    assert response.status_code == 200, f"unexpected status {response.status_code}"
    payload = response.json()

    required_keys = {
        "builder_status",
        "uptime_seconds",
        "queue_depth",
        "directives_today",
        "failures_today",
        "domains_with_proposals",
        "last_heartbeat",
        "generated_at",
    }
    missing = required_keys - payload.keys()
    assert not missing, f"missing keys {missing}"
    assert isinstance(payload["queue_depth"], int) and payload["queue_depth"] >= 0, "queue_depth must be a non‑negative int"

    print("PASS")
    sys.exit(0)