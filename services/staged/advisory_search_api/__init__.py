# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, User, Org


class ServerRegistry(BaseModel):
    id: int
    server_name: str
    server_type: str | None = None
    status: str | None = None
    registered_at: datetime | None = None

    class Config:
        from_attributes = True


class MeshScoreResponse(BaseModel):
    scores: List[dict]
    endpoint: str


class MeshMemoryResponse(BaseModel):
    memory: dict | None = None
    endpoint: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: str | None = None

    class Config:
        from_attributes = True


class Users(BaseModel):
    items: List[UserResponse]
    total: int


class ScoreDisputes(BaseModel):
    items: List[dict]
    total: int


def _query_bus(path: str, params: dict | None = None) -> dict:
    import urllib.request

    url = f"http://127.0.0.1:8772{path}"
    data = json.dumps(params or {}).encode() if params else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def mesh_scores_endpoint() -> MeshScoreResponse:
    try:
        result = _query_bus("/query", {"sql": "SELECT * FROM mcp_signal_scores LIMIT 100"})
        scores = result.get("rows", [])
    except Exception:
        scores = []
    return MeshScoreResponse(scores=scores, endpoint="/query:mcp_signal_scores")


def dummy_post_api() -> dict:
    return {"status": "ok", "endpoint": "dummy_post_api"}


def get_users(session: Session = Depends(get_session)) -> Users:
    stmt = select(User).limit(100)
    rows = session.execute(stmt).scalars().all()
    items = [UserResponse.model_validate(u) for u in rows]
    return Users(items=items, total=len(items))


def get_server_registries(session: Session = Depends(get_session)) -> List[ServerRegistry]:
    stmt = select(McpServerRegistry).limit(100)
    rows = session.execute(stmt).scalars().all()
    return [ServerRegistry.model_validate(r) for r in rows]


def get_mesh_memory_endpoint() -> MeshMemoryResponse:
    try:
        result = _query_bus("/query", {"sql": "SELECT * FROM mesh_memory LIMIT 10"})
        memory = result.get("rows", [{}])[0] if result.get("rows") else None
    except Exception:
        memory = None
    return MeshMemoryResponse(memory=memory, endpoint="/query:mesh_memory")


def mesh_memory_endpoint_get(memory_id: int | None = None) -> MeshMemoryResponse:
    if memory_id:
        try:
            result = _query_bus("/query", {"sql": f"SELECT * FROM mesh_memory WHERE id = {memory_id}"})
            memory = result.get("rows", [{}])[0] if result.get("rows") else None
        except Exception:
            memory = None
    else:
        memory = None
    return MeshMemoryResponse(memory=memory, endpoint="/query:mesh_memory")


def run_self_test() -> str:
    try:
        _ = _query_bus("/health")
        return "PASS"
    except Exception:
        return "FAIL"


def mesh_scores() -> List[dict]:
    try:
        result = _query_bus("/query", {"sql": "SELECT * FROM mcp_signal_scores LIMIT 50"})
        return result.get("rows", [])
    except Exception:
        return []


def signal_scores_endpoint() -> dict:
    try:
        result = _query_bus("/query", {"sql": "SELECT * FROM mcp_signal_scores"})
        return {"scores": result.get("rows", []), "count": len(result.get("rows", []))}
    except Exception:
        return {"scores": [], "count": 0}


def get_mesh_memory_by_id(memory_id: int, session: Session = Depends(get_session)) -> MeshMemoryResponse:
    try:
        result = _query_bus("/query", {"sql": f"SELECT * FROM mesh_memory WHERE id = {memory_id}"})
        memory = result.get("rows", [{}])[0] if result.get("rows") else None
    except Exception:
        memory = None
    return MeshMemoryResponse(memory=memory, endpoint="/query:mesh_memory")


def mesh_memory_endpoint(memory_id: int | None = None) -> MeshMemoryResponse:
    return mesh_memory_endpoint_get(memory_id)


def users_endpoint(user_id: int | None = None, session: Session = Depends(get_session)) -> UserResponse | Users:
    if user_id:
        user = session.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return UserResponse.model_validate(user)
    return get_users(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session

    app = FastAPI()

    @app.get("/test")
    def test():
        return {"result": run_self_test()}

    result = run_self_test()
    print(result)