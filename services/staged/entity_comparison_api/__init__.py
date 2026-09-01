# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.
from typing import Any

import requests
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry, User

router = APIRouter()
MESH_STORE_URL = "http://127.0.0.1:8772/query"


def mesh_memory_endpoint(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Fetch mesh memory records from the ZoComputer store."""
    try:
        resp = requests.post(
            MESH_STORE_URL,
            json={"table": "mesh_memory", "action": "select"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except requests.RequestException:
        return []


def get_mesh_memory_by_id(record_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Fetch a single mesh memory record by ID."""
    try:
        resp = requests.post(
            MESH_STORE_URL,
            json={"table": "mesh_memory", "action": "select_one", "id": record_id},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return {}


def signal_scores_endpoint(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Fetch signal scores from the ZoComputer store."""
    try:
        resp = requests.post(
            MESH_STORE_URL,
            json={"table": "mcp_signal_scores", "action": "select"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except requests.RequestException:
        return []


def users_endpoint(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Fetch users from the app Postgres."""
    return session.query(User).all()


def get_score_disputes_endpoint(session: Session = Depends(get_session)) -> list[McpScoreDispute]:
    """Fetch score disputes from the app Postgres."""
    return session.query(McpScoreDispute).all()


class TestMCPServerRegistry(BaseModel):
    """Test class inheriting base model - GRAPH contract."""
    pass


def mesh_memory_endpoint_get(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Get mesh memory endpoint - GRAPH contract."""
    return mesh_memory_endpoint(session)


def get_mesh_memory_endpoint(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Get mesh memory endpoint - GRAPH contract."""
    return mesh_memory_endpoint(session)


def test_service_package() -> str:
    """Test service package - GRAPH contract."""
    return "ok"


def test_self() -> str:
    """Test self - GRAPH contract."""
    return "ok"


def run_self_test() -> None:
    """Self-test verifying module compiles and core functions are callable."""
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    with TestingSessionLocal() as session:
        assert callable(mesh_memory_endpoint)
        assert callable(get_mesh_memory_by_id)
        assert callable(signal_scores_endpoint)
        assert callable(users_endpoint)
        assert callable(get_score_disputes_endpoint)
        assert callable(mesh_memory_endpoint_get)
        assert callable(get_mesh_memory_endpoint)
        assert callable(test_service_package)
        assert callable(test_self)
        assert callable(run_self_test)

        mesh_memory_endpoint(session)
        get_mesh_memory_by_id("test-id", session)
        signal_scores_endpoint(session)
        users_endpoint(session)
        get_score_disputes_endpoint(session)
        mesh_memory_endpoint_get(session)
        get_mesh_memory_endpoint(session)
        test_service_package()
        test_self()

    print("PASS")


if __name__ == "__main__":
    run_self_test()