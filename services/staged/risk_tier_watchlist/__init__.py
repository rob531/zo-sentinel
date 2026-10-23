from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
import httpx

router = APIRouter()

MESH_API = "http://127.0.0.1:8772/query"
BUS_TABLES = {
    "mcp_signal_scores",
    "mesh_memory",
    "mcp_server_registry",
    "mcp_llm_axis_scores",
    "mcp_score_disputes",
    "e2e_axis_scores",
    "e2e_servers",
}


class Users(User):
    pass


class ScoreDisputes(McpScoreDispute):
    pass


class TestMCPServerRegistry(McpServerRegistry):
    pass


def _mesh_query(sql: str) -> list[dict]:
    with httpx.Client(timeout=10) as client:
        resp = client.post(MESH_API, json={"sql": sql})
        resp.raise_for_status()
        return resp.json()


def get_mesh_memory_endpoint(
    session: Session = Depends(get_session),
    memory_id: Optional[str] = None,
    server_id: Optional[str] = None,
) -> dict:
    if memory_id:
        rows = _mesh_query(f"SELECT * FROM mesh_memory WHERE id = '{memory_id}' LIMIT 1")
    elif server_id:
        rows = _mesh_query(f"SELECT * FROM mesh_memory WHERE server_id = '{server_id}' LIMIT 1")
    else:
        rows = _mesh_query("SELECT * FROM mesh_memory LIMIT 100")
    return {"data": rows}


def mesh_memory_endpoint_get(
    memory_id: str,
    session: Session = Depends(get_session),
) -> dict:
    return get_mesh_memory_endpoint(session=session, memory_id=memory_id)


def mesh_memory_endpoint(
    session: Session = Depends(get_session),
    server_id: Optional[str] = None,
) -> dict:
    return get_mesh_memory_endpoint(session=session, server_id=server_id)


def get_mesh_memory_by_id(
    memory_id: str,
    session: Session = Depends(get_session),
) -> dict:
    return mesh_memory_endpoint_get(memory_id=memory_id, session=session)


def mesh_scores_endpoint(
    session: Session = Depends(get_session),
    server_id: Optional[str] = None,
    days: int = 30,
) -> dict:
    if server_id:
        rows = _mesh_query(
            f"SELECT * FROM mcp_signal_scores WHERE server_id = '{server_id}' AND days <= {days} LIMIT 100"
        )
    else:
        rows = _mesh_query(f"SELECT * FROM mcp_signal_scores WHERE days <= {days} LIMIT 100")
    return {"data": rows}


def mesh_scores(
    session: Session = Depends(get_session),
    server_id: Optional[str] = None,
) -> dict:
    return mesh_scores_endpoint(session=session, server_id=server_id)


def signal_scores_endpoint(
    session: Session = Depends(get_session),
    server_id: Optional[str] = None,
) -> dict:
    return mesh_scores(session=session, server_id=server_id)


def get_users(session: Session = Depends(get_session)) -> list[dict]:
    users = session.query(User).all()
    return [{"id": u.id, "email": u.email, "name": u.name} for u in users]


def users_endpoint(session: Session = Depends(get_session)) -> list[dict]:
    return get_users(session=session)


def dummy_post_api(
    data: dict,
    session: Session = Depends(get_session),
) -> dict:
    return {"status": "ok", "received": data}


def get_server_registries(session: Session = Depends(get_session)) -> list[dict]:
    regs = session.query(McpServerRegistry).all()
    return [{"id": r.id, "name": r.name, "server_type": r.server_type} for r in regs]


def run_self_test(session: Session = Depends(get_session)) -> dict:
    try:
        _ = session.query(McpServerRegistry).first()
        _ = session.query(McpLlmAxisScore).first()
        _ = session.query(McpScoreDispute).first()
        _ = session.query(User).first()
        _ = session.query(Org).first()
        _ = _mesh_query("SELECT 1 FROM mcp_signal_scores LIMIT 1")
        _ = _mesh_query("SELECT 1 FROM mesh_memory LIMIT 1")
        return {"status": "PASS"}
    except Exception as e:
        return {"status": "FAIL", "error": str(e)}


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
    TestingSession = sessionmaker(bind=engine)

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    result = run_self_test(next(override_get_session()))
    print(result["status"])