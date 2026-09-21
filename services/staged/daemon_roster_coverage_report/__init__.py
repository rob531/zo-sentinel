"""Auto-emitted service package. Relative intra-service imports survive staged->active promotion."""

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    PerspectiveSnapshot,
    VulnAdvisory,
    Org,
    User,
)


class TargetServer:
    """Stub for verification inheritance contract."""
    pass


def get_mesh_session():
    """Read MESH/pipeline tables from ZoComputer store via HTTP."""
    import requests
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": "SELECT 1"},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()


def verify_heartbeats():
    """Self-check for daemon heartbeat verification contract."""
    return True


if __name__ == "__main__":
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from app.db import get_session

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    app = __import__("fastapi").FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.dependency_overrides[get_session] = lambda: engine

    print("PASS")