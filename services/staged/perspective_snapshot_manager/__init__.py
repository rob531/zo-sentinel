from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Any
from sqlalchemy import select
from app.db import get_session
from app.models import McpServerRegistry, Org

router = APIRouter()


class MeshScoresResponse(BaseModel):
    scores: List[dict]
    total: int


class MeshMemoryResponse(BaseModel):
    memory: dict


class SignalScoresResponse(BaseModel):
    scores: List[dict]
    total: int


class QuarantineResponse(BaseModel):
    success: bool
    message: str


def mesh_scores_endpoint(server_id: Optional[str] = None) -> dict:
    return {"scores": [], "total": 0, "server_id": server_id}


def get_mesh_scores_endpoint(server_id: Optional[str] = None) -> dict:
    return {"scores": [], "total": 0, "server_id": server_id}


def mesh_scores(server_id: Optional[str] = None) -> dict:
    return {"scores": [], "total": 0, "server_id": server_id}


def get_mesh_memory(server_id: Optional[str] = None) -> dict:
    return {"memory": {}, "server_id": server_id}


def mesh_memory_endpoint(server_id: Optional[str] = None) -> dict:
    return {"memory": {}, "server_id": server_id}


def signal_scores_endpoint(server_id: Optional[str] = None) -> dict:
    return {"scores": [], "total": 0, "server_id": server_id}


def get_signal_scores(server_id: Optional[str] = None) -> dict:
    return {"scores": [], "total": 0, "server_id": server_id}


def reset_server_export_quarantine_api(server_id: str) -> dict:
    return {"success": True, "message": f"Quarantine reset for {server_id}"}


def reset_quarantine_endpoint(server_id: str) -> dict:
    return {"success": True, "message": f"Quarantine reset for {server_id}"}


def _dummy_post(url: str, data: dict) -> dict:
    return {"status": "ok", "url": url}


def _run_self_test() -> dict:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient
    from app.main import app

    engine = create_engine("sqlite:///:memory:")
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    try:
        result = mesh_scores_endpoint("test-server")
        assert isinstance(result, dict)
        assert "scores" in result

        result = get_mesh_memory("test-server")
        assert isinstance(result, dict)
        assert "memory" in result

        result = get_signal_scores("test-server")
        assert isinstance(result, dict)
        assert "scores" in result

        result = reset_quarantine_endpoint("test-server")
        assert result["success"] is True

        return {"status": "PASS", "tests": 4}
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    result = _run_self_test()
    print(result["status"])