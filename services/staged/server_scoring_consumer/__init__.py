"""Auto-emitted service package for staged services."""

from typing import Any, Optional

import requests
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, User, Org

BUS_URL = "http://127.0.0.1:8772"


def get_users(db: Session = Depends(get_session)) -> list[User]:
    return db.query(User).all()


def users_endpoint(db: Session = Depends(get_session)) -> list[dict[str, Any]]:
    users = db.query(User).all()
    return [{"id": u.id, "name": u.name, "email": u.email} for u in users]


def dummy_post_api(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "received": data}


class Users:
    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def all(self) -> list[User]:
        return self.db.query(User).all()


class ScoreDisputes:
    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def all(self) -> list[McpScoreDispute]:
        return self.db.query(McpScoreDispute).all()


def get_server_registries(db: Session = Depends(get_session)) -> list[McpServerRegistry]:
    return db.query(McpServerRegistry).all()


def mesh_scores_endpoint(
    server_id: Optional[str] = None, db: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    payload = {}
    if server_id:
        payload["filters"] = {"server_id": server_id}
    resp = requests.post(f"{BUS_URL}/query", json={"table": "mcp_signal_scores", **payload})
    resp.raise_for_status()
    return resp.json().get("rows", [])


def mesh_scores(server_id: Optional[str] = None) -> list[dict[str, Any]]:
    return mesh_scores_endpoint(server_id=server_id)


def signal_scores_endpoint(server_id: Optional[str] = None) -> list[dict[str, Any]]:
    return mesh_scores_endpoint(server_id=server_id)


def get_mesh_memory_endpoint(
    memory_id: Optional[str] = None, db: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    if memory_id:
        resp = requests.post(
            f"{BUS_URL}/query",
            json={"table": "mesh_memory", "filters": {"memory_id": memory_id}},
        )
    else:
        resp = requests.post(f"{BUS_URL}/query", json={"table": "mesh_memory"})
    resp.raise_for_status()
    return resp.json().get("rows", [])


def get_mesh_memory_by_id(memory_id: str) -> Optional[dict[str, Any]]:
    rows = get_mesh_memory_endpoint(memory_id=memory_id)
    return rows[0] if rows else None


def mesh_memory_endpoint(
    memory_id: Optional[str] = None, db: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    return get_mesh_memory_endpoint(memory_id=memory_id)


def mesh_memory_endpoint_get(memory_id: str) -> Optional[dict[str, Any]]:
    return get_mesh_memory_by_id(memory_id)


def run_self_test() -> str:
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE orgs (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT, org_id INTEGER)"))
        conn.execute(text("INSERT INTO orgs (id, name) VALUES (1, 'test-org')"))
        conn.execute(text("INSERT INTO users (id, name, email, org_id) VALUES (1, 'test', 't@t.com', 1)"))

    app = FastAPI()

    @app.get("/test")
    def test_route():
        return {"ok": True}

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    return "PASS"


if __name__ == "__main__":
    print(run_self_test())