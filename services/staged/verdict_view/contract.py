# services/staged/verdict_view/contract.py
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict

from app.db import get_session, Base
from app.models import McpServerRegistry  # real data layer import

router = APIRouter(prefix="/api")


class AxisScore(BaseModel):
    label: str
    p_top: float


class VerdictResponse(BaseModel):
    server_id: str
    name: str
    verdict: str
    risk_tier: str
    axes: Dict[str, AxisScore] = {}


@router.get(
    "/servers/{server_id}/verdict",
    response_model=VerdictResponse,
    tags=["verdict_view"],
)
def get_verdict(
    server_id: str,
    session=Depends(get_session),
):
    """Return verdict information for a given server."""
    server = (
        session.query(McpServerRegistry)
        .filter_by(server_id=server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Minimal placeholder logic – other services may enrich this later.
    return VerdictResponse(
        server_id=server_id,
        name=getattr(server, "name", ""),
        verdict="OK",
        risk_tier="low",
        axes={},
    )


# --------------------------------------------------------------------------- #
# Self‑test (runnable with `python -m services.staged.verdict_view.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create an in‑memory SQLite DB that mirrors the real models
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    # Override the app's session dependency with the test session
    def get_test_session():
        with TestSession() as s:
            yield s

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # Seed a single server record
    test_server_id = "test123"
    with TestSession() as s:
        s.add(
            McpServerRegistry(
                server_id=test_server_id,
                name="Test Server",
                confidence=1.0,  # any required column with a sensible default
            )
        )
        s.commit()

    # Perform the request
    resp = client.get(f"/api/servers/{test_server_id}/verdict")
    try:
        assert resp.status_code == 200
        data = resp.json()
        assert data["server_id"] == test_server_id
    except AssertionError:
        sys.exit(1)

    print("PASS")
    sys.exit(0)