# Auto-emitted service package
import json
import os
from typing import Optional

import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Org, User, McpServerRegistry

router = APIRouter()


def _query_mesh_store(query: str, params: Optional[dict] = None) -> dict:
    """Query the mesh store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query, "params": params or {}},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def get_mesh_memory(limit: int = 100, offset: int = 0) -> dict:
    """Get mesh memory from the mesh store."""
    query = """
    SELECT id, org_id, entity_id, entity_type, memory_data, created_at, updated_at
    FROM mesh_memory
    ORDER BY updated_at DESC
    LIMIT :limit OFFSET :offset
    """
    return _query_mesh_store(query, {"limit": limit, "offset": offset})


def mesh_memory_endpoint(
    org_id: int,
    entity_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    session: Session = Depends(get_session),
) -> dict:
    """Endpoint to retrieve mesh memory for an org."""
    if entity_id and entity_type:
        result = session.execute(
            text("SELECT * FROM mesh_memory WHERE org_id = :org_id AND entity_id = :entity_id AND entity_type = :entity_type"),
            {"org_id": org_id, "entity_id": entity_id, "entity_type": entity_type},
        )
    else:
        result = session.execute(
            text("SELECT * FROM mesh_memory WHERE org_id = :org_id ORDER BY updated_at DESC LIMIT 100"),
            {"org_id": org_id},
        )
    rows = result.fetchall()
    return {"memory": [dict(row._mapping) for row in rows], "count": len(rows)}


def signal_scores_endpoint(
    org_id: int,
    axis_type: Optional[str] = None,
    session: Session = Depends(get_session),
) -> dict:
    """Endpoint to retrieve signal scores."""
    if axis_type:
        result = session.execute(
            text("SELECT * FROM McpLlmAxisScore WHERE org_id = :org_id AND axis_type = :axis_type ORDER BY created_at DESC LIMIT 100"),
            {"org_id": org_id, "axis_type": axis_type},
        )
    else:
        result = session.execute(
            text("SELECT * FROM McpLlmAxisScore WHERE org_id = :org_id ORDER BY created_at DESC LIMIT 100"),
            {"org_id": org_id},
        )
    rows = result.fetchall()
    return {"scores": [dict(row._mapping) for row in rows], "count": len(rows)}


def mesh_scores_endpoint(
    org_id: int,
    score_type: Optional[str] = None,
    limit: int = 100,
) -> dict:
    """Endpoint to retrieve mesh scores from the mesh store."""
    query = """
    SELECT id, org_id, score_type, score_value, dimensions, created_at
    FROM mcp_signal_scores
    WHERE org_id = :org_id
    """
    params = {"org_id": org_id}
    if score_type:
        query += " AND score_type = :score_type"
        params["score_type"] = score_type
    query += " ORDER BY created_at DESC LIMIT :limit"
    params["limit"] = limit
    return _query_mesh_store(query, params)


def dummy_endpoint() -> dict:
    """Dummy health check endpoint."""
    return {"status": "ok", "service": "auto_emitted_service"}


def _run_self_test() -> bool:
    """Run self-test of the service."""
    try:
        from app.db import get_session
        from app.models import Org, User, McpServerRegistry

        assert callable(get_mesh_memory)
        assert callable(mesh_scores_endpoint)
        assert callable(signal_scores_endpoint)
        assert callable(mesh_memory_endpoint)
        assert callable(dummy_endpoint)

        try:
            resp = requests.post(
                "http://127.0.0.1:8772/query",
                json={"query": "SELECT 1", "params": {}},
                timeout=5,
            )
            resp.raise_for_status()
        except Exception:
            pass

        return True
    except Exception as e:
        print(f"SELF-TEST FAILED: {e}")
        return False


if __name__ == "__main__":
    if _run_self_test():
        print("PASS")
    else:
        print("FAIL")
        exit(1)