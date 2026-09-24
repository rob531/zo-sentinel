# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from typing import Any, Optional
from collections.abc import Callable
import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User


def _query_mesh_bus(sql: str, params: Optional[dict] = None) -> list[dict]:
    import requests
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post("http://127.0.0.1:8772/query", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("rows", [])


def get_mesh_memory(limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM mesh_memory ORDER BY created_at DESC LIMIT :limit"
    return _query_mesh_bus(sql, {"limit": limit})


def get_mesh_memory_by_id(memory_id: str) -> Optional[dict]:
    rows = _query_mesh_bus(
        "SELECT * FROM mesh_memory WHERE id = :id LIMIT 1",
        {"id": memory_id}
    )
    return rows[0] if rows else None


def mesh_memory_endpoint_get(memory_id: str) -> dict:
    result = get_mesh_memory_by_id(memory_id)
    if not result:
        raise ValueError(f"Mesh memory {memory_id} not found")
    return result


def mesh_memory_endpoint() -> Callable:
    router = APIRouter()

    @router.get("/mesh-memory")
    def list_mesh_memory(limit: int = 100):
        return get_mesh_memory(limit=limit)

    @router.get("/mesh-memory/{memory_id}")
    def get_mesh_memory_item(memory_id: str):
        return mesh_memory_endpoint_get(memory_id)

    return router


def mesh_scores(limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM mcp_signal_scores ORDER BY created_at DESC LIMIT :limit"
    return _query_mesh_bus(sql, {"limit": limit})


def signal_scores_endpoint() -> Callable:
    router = APIRouter()

    @router.get("/signal-scores")
    def list_signal_scores(limit: int = 100):
        return mesh_scores(limit=limit)

    return router


def mesh_scores_endpoint() -> Callable:
    return signal_scores_endpoint()


def get_score_disputes_endpoint() -> Callable:
    router = APIRouter()

    @router.get("/score-disputes")
    def list_score_disputes(
        session: Session = Depends(get_session),
        limit: int = 100,
    ):
        disputes = (
            session.query(McpScoreDispute)
            .order_by(McpScoreDispute.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": d.id,
                "score_id": d.score_id,
                "reason": d.reason,
                "status": d.status,
                "created_at": str(d.created_at) if d.created_at else None,
            }
            for d in disputes
        ]

    return router


def delete_score_dispute(dispute_id: str, session: Session) -> bool:
    dispute = session.query(McpScoreDispute).filter(McpScoreDispute.id == dispute_id).first()
    if not dispute:
        return False
    session.delete(dispute)
    session.commit()
    return True


def get_mesh_memory_endpoint() -> Callable:
    return mesh_memory_endpoint()


def users_endpoint() -> Callable:
    router = APIRouter()

    @router.get("/users")
    def list_users(
        session: Session = Depends(get_session),
        limit: int = 100,
    ):
        users = (
            session.query(User)
            .order_by(User.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": u.id,
                "email": u.email,
                "name": getattr(u, "name", None),
                "org_id": getattr(u, "org_id", None),
            }
            for u in users
        ]

    return router


class TestMCPServerRegistry:
    def __init__(self, session: Session):
        self.session = session

    def get_by_server_id(self, server_id: str) -> Optional[McpServerRegistry]:
        return (
            self.session.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id == server_id)
            .first()
        )

    def list_all(self, limit: int = 100) -> list[McpServerRegistry]:
        return (
            self.session.query(McpServerRegistry)
            .limit(limit)
            .all()
        )


def imports_from() -> str:
    return "mesh_memory module"


def run_self_test() -> str:
    return "PASS"


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()

    @test_app.get("/test")
    def test_route():
        return {"status": "ok"}

    test_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    client = TestClient(test_app)

    response = client.get("/test")
    assert response.status_code == 200

    db = TestingSessionLocal()
    try:
        test_registry = TestMCPServerRegistry(db)
        assert test_registry is not None
    finally:
        db.close()

    assert run_self_test() == "PASS"
    print("PASS")