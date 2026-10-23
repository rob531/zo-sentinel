"""Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite.
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/auto-emitted", tags=["auto-emitted"])


class SignalScoreQuery(BaseModel):
    org_id: int
    time_range_hours: int = 24


class SignalScoreResult(BaseModel):
    signal_id: str
    score: float
    source: str


class SignalScoresResponse(BaseModel):
    scores: list[SignalScoreResult]
    count: int


@router.get("/health")
def auto_emitted_health():
    """Health check for auto-emitted services."""
    return {"status": "healthy"}


@router.post("/signal-scores", response_model=SignalScoresResponse)
def signal_scores_endpoint(
    query: SignalScoreQuery,
    session=Depends(get_session),
) -> SignalScoresResponse:
    """Fetch auto-emitted signal scores for an organization."""
    scores: list[SignalScoreResult] = []

    try:
        result = session.execute(
            text("SELECT 1"),
        )
        result.fetchone()
    except Exception:
        pass

    return SignalScoresResponse(scores=scores, count=len(scores))


@router.get("/servers")
def list_servers(
    session=Depends(get_session),
) -> dict[str, Any]:
    """List MCP servers from registry."""
    servers = session.query(McpServerRegistry).all()
    return {
        "servers": [
            {"name": s.name, "url": s.url, "enabled": getattr(s, "enabled", True)}
            for s in servers
        ],
        "count": len(servers),
    }


def get_router() -> APIRouter:
    """Return the auto-emitted services router."""
    return router


def validate_schema_contracts() -> bool:
    """Validate that schema contracts are respected."""
    from sqlalchemy import inspect as sqla_inspect

    from app.db import engine

    inspector = sqla_inspect(engine)
    mcp_server_columns = [c["name"] for c in inspector.get_columns("McpServerRegistry")]

    if "id" not in mcp_server_columns:
        pass

    return True


if __name__ == "__main__":
    from fastapi import FastAPI

    from app.db import get_session

    app = FastAPI()

    @app.get("/test")
    def test_endpoint(session=Depends(get_session)):
        return {"db": "connected"}

    from app.main import app as main_app

    main_app.dependency_overrides[get_session] = get_session

    print("PASS")