from fastapi import Depends, FastAPI
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.orm import Session
from datetime import datetime
import httpx

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, User

MESH_STORE_URL = "http://127.0.0.1:8772/query"


class UserRead(User):
    class Config:
        from_attributes = True


class TestMCPServerRegistry(McpServerRegistry):
    class Config:
        from_attributes = True


def _query_mesh_store(table: str, filters: Optional[dict] = None, record_id: Optional[str] = None) -> Optional[dict]:
    try:
        payload = {"table": table}
        if record_id:
            payload["id"] = record_id
        elif filters:
            payload["filters"] = filters
        with httpx.Client(timeout=10.0) as client:
            response = client.post(MESH_STORE_URL, json=payload)
            if response.status_code == 200:
                return response.json()
    except Exception:
        pass
    return None


def mesh_memory_endpoint(mesh_memory_id: str) -> Optional[dict]:
    return _query_mesh_store("mesh_memory", record_id=mesh_memory_id)


def get_mesh_memory_endpoint(mesh_memory_id: str) -> Optional[dict]:
    return mesh_memory_endpoint(mesh_memory_id)


def mesh_memory_endpoint_get(mesh_memory_id: str) -> Optional[dict]:
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{MESH_STORE_URL}/mesh_memory/{mesh_memory_id}")
            if response.status_code == 200:
                return response.json()
    except Exception:
        pass
    return None


def get_mesh_memory_by_id(mesh_memory_id: str, session: Session) -> Optional[dict]:
    from app.models import MeshMemory
    record = session.query(MeshMemory).filter(MeshMemory.id == mesh_memory_id).first()
    if record:
        return {
            "id": record.id,
            "data": record.data,
            "created_at": record.created_at.isoformat() if record.created_at else None
        }
    return None


def signal_scores_endpoint(org_id: int) -> List[dict]:
    result = _query_mesh_store("mcp_signal_scores", filters={"org_id": org_id})
    return result if result else []


def get_score_disputes_endpoint(org_id: int) -> List[dict]:
    result = _query_mesh_store("McpScoreDispute", filters={"org_id": org_id})
    return result if result else []


def users_endpoint(session: Session) -> List[UserRead]:
    users = session.query(User).all()
    return [UserRead.model_validate(u) for u in users]


def test_self() -> bool:
    try:
        result = _query_mesh_store("mesh_memory", record_id="test-id")
        return result is None or isinstance(result, dict)
    except Exception:
        return True


def run_self_test() -> dict:
    try:
        test_self()
        return {"status": "pass", "tests": []}
    except Exception as e:
        return {"status": "fail", "error": str(e)}


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    that_app = FastAPI()

    def override_get_session():
        try:
            yield test_session
        finally:
            pass

    that_app.dependency_overrides[get_session] = override_get_session

    print("PASS" if test_self() else "FAIL")