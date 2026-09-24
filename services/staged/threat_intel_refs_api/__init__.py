"""Auto-emitted service package."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
import httpx

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

router = APIRouter()


class MCPLLMAxisScoresModel(BaseModel):
    id: int
    server_id: int
    axis_name: str
    score: float
    computed_at: Optional[str] = None

    class Config:
        from_attributes = True


class MeshMemoryModel(BaseModel):
    key: str
    value: Any
    updated_at: Optional[str] = None


class SignalScoreModel(BaseModel):
    id: int
    signal_type: str
    score: float
    metadata: Optional[Dict[str, Any]] = None


async def _post_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Post query to write-service bus."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://127.0.0.1:8772/query",
            json={"sql": sql, "params": params or {}}
        )
        response.raise_for_status()
        return response.json()


async def get_mesh_memory(key: str) -> Optional[Dict[str, Any]]:
    """Retrieve mesh memory by key."""
    result = await _post_query(
        "SELECT * FROM mesh_memory WHERE key = %s LIMIT 1",
        {"key": key}
    )
    return result[0] if result else None


async def mesh_scores_endpoint(
    server_id: int,
    session: Any = Depends(get_session)
) -> List[MCPLLMAxisScoresModel]:
    """Get mesh scores for a server."""
    scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()
    return [MCPLLMAxisScoresModel.model_validate(s) for s in scores]


async def signal_scores_endpoint(
    signal_type: Optional[str] = None,
    session: Any = Depends(get_session)
) -> List[Dict[str, Any]]:
    """Get signal scores from the bus."""
    sql = "SELECT * FROM mcp_signal_scores"
    params = {}
    if signal_type:
        sql += " WHERE signal_type = %(signal_type)s"
        params["signal_type"] = signal_type
    return await _post_query(sql, params)


async def mesh_memory_endpoint(
    key: str,
    value: Any,
    session: Any = Depends(get_session)
) -> Dict[str, Any]:
    """Store mesh memory entry."""
    result = await _post_query(
        "INSERT INTO mesh_memory (key, value, updated_at) VALUES (%(key)s, %(value)s, NOW()) RETURNING *",
        {"key": key, "value": str(value)}
    )
    return result[0] if result else {}


async def mesh_memory_endpoint_get(
    key: str,
    session: Any = Depends(get_session)
) -> Optional[MeshMemoryModel]:
    """Get mesh memory entry by key."""
    memory = await get_mesh_memory(key)
    if memory:
        return MeshMemoryModel(**memory)
    return None


async def orgs_endpoint(
    session: Any = Depends(get_session)
) -> List[Dict[str, Any]]:
    """Get all organizations."""
    from app.models import Org
    orgs = session.query(Org).all()
    return [{"id": o.id, "name": o.name} for o in orgs]


def get_db():
    """Get database session dependency."""
    return get_session


async def get_score_disputes_endpoint(
    server_id: Optional[int] = None,
    session: Any = Depends(get_session)
) -> List[McpScoreDispute]:
    """Get score disputes."""
    query = session.query(McpScoreDispute)
    if server_id:
        query = query.filter(McpScoreDispute.server_id == server_id)
    return query.all()


async def get_mesh_memory_endpoint(
    key: str,
    session: Any = Depends(get_session)
) -> Optional[Dict[str, Any]]:
    """Get mesh memory by key (alias)."""
    return await get_mesh_memory(key)


def get_org_by_id(org_id: int, session: Any = Depends(get_session)) -> Optional[Dict[str, Any]]:
    """Get organization by ID."""
    from app.models import Org
    org = session.query(Org).filter(Org.id == org_id).first()
    if org:
        return {"id": org.id, "name": org.name}
    return None


async def _run_self_test() -> bool:
    """Run self-test validation."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post("http://127.0.0.1:8772/query", json={"sql": "SELECT 1", "params": {}})
            if resp.status_code != 200:
                return False
        return True
    except Exception:
        return False


def test_service_package() -> str:
    """Test the service package."""
    import asyncio
    return asyncio.run(_run_self_test())


if __name__ == "__main__":
    result = test_service_package()
    if result:
        print("PASS")
    else:
        print("FAIL")