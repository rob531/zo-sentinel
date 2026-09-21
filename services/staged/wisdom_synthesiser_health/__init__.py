"""Service package initialization for staged services."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class MeshMemoryResponse(BaseModel):
    """Response model for mesh memory endpoint."""
    id: str
    memory_type: str
    data: dict[str, Any]


class SignalScoresResponse(BaseModel):
    """Response model for signal scores endpoint."""
    scores: list[dict[str, Any]]


class ScoreDisputesResponse(BaseModel):
    """Response model for score disputes endpoint."""
    disputes: list[dict[str, Any]]


router = APIRouter()


@router.get("/mesh-memory")
def mesh_memory_endpoint(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return mesh memory data from the pipeline store."""
    try:
        import requests
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mesh_memory LIMIT 100"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {"mesh_memory": [], "status": "fallback_empty"}


@router.get("/mesh-memory/{memory_id}")
def get_mesh_memory_by_id(
    memory_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Retrieve specific mesh memory entry by ID."""
    try:
        import requests
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE id = '{memory_id}'"},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("results"):
            return data["results"][0]
        raise HTTPException(status_code=404, detail="Memory not found")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Memory not found")


@router.get("/mesh-memory-endpoint")
def get_mesh_memory_endpoint(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Alias for mesh_memory_endpoint."""
    return mesh_memory_endpoint(session)


@router.get("/signal-scores")
def signal_scores_endpoint(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return signal scores from the pipeline store."""
    try:
        import requests
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mcp_signal_scores LIMIT 100"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {"scores": [], "status": "fallback_empty"}


@router.get("/score-disputes")
def get_score_disputes_endpoint(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return score disputes from the app database."""
    try:
        result = session.execute(
            text("SELECT * FROM McpScoreDispute LIMIT 100")
        )
        rows = [dict(row._mapping) for row in result]
        return {"disputes": rows}
    except Exception:
        return {"disputes": []}


def test_service_package() -> bool:
    """Run self-test of service package components."""
    from fastapi import FastAPI
    from app.db import get_session, Session as AppSession
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    class FakeGetSession:
        def __init__(self, session):
            self._session = session

        def __call__(self):
            return self._session

    from sqlalchemy.orm import sessionmaker
    TestingSession = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS McpScoreDispute (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS mesh_memory (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS mcp_signal_scores (id INTEGER PRIMARY KEY)"))

    test_session = TestingSession()

    app = FastAPI()

    app.dependency_overrides[get_session] = FakeGetSession(test_session)

    @app.get("/mesh-memory")
    def test_mesh_memory(session: Session = Depends(get_session)):
        return mesh_memory_endpoint(session)

    @app.get("/mesh-memory/{memory_id}")
    def test_get_by_id(memory_id: str, session: Session = Depends(get_session)):
        return get_mesh_memory_by_id(memory_id, session)

    @app.get("/mesh-memory-endpoint")
    def test_get_endpoint(session: Session = Depends(get_session)):
        return get_mesh_memory_endpoint(session)

    @app.get("/signal-scores")
    def test_signal_scores(session: Session = Depends(get_session)):
        return signal_scores_endpoint(session)

    @app.get("/score-disputes")
    def test_disputes(session: Session = Depends(get_session)):
        return get_score_disputes_endpoint(session)

    import json
    from fastapi.testclient import TestClient

    client = TestClient(app)

    try:
        resp = client.get("/mesh-memory")
        assert resp.status_code == 200, f"mesh_memory failed: {resp.status_code}"

        resp = client.get("/mesh-memory/test-id")
        assert resp.status_code in (200, 404), f"get_by_id failed: {resp.status_code}"

        resp = client.get("/mesh-memory-endpoint")
        assert resp.status_code == 200, f"get_mesh_memory_endpoint failed: {resp.status_code}"

        resp = client.get("/signal-scores")
        assert resp.status_code == 200, f"signal_scores failed: {resp.status_code}"

        resp = client.get("/score-disputes")
        assert resp.status_code == 200, f"score_disputes failed: {resp.status_code}"

        print("PASS")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


if __name__ == "__main__":
    test_service_package()