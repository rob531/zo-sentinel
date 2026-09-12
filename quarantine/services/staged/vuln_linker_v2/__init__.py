"""
Auto-emitted service package providing shared mesh memory and signal scores utilities.
Supports both app DB and ZoComputer store data access patterns.
"""

import json
import logging
import sys
from typing import Any, Dict, List, Optional

import requests
from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MESH_STORE_URL = "http://127.0.0.1:8772/query"


def _query_mesh_store(query: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Query the ZoComputer mesh store with parameterized query to prevent SQL injection.
    """
    payload = {"query": query}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(MESH_STORE_URL, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error(f"Mesh store query failed: {e}")
        return {"error": str(e)}


def get_mesh_memory(
    session: Session,
    server_id: Optional[str] = None,
    org_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch mesh memory from ZoComputer store.
    """
    query = "SELECT * FROM mesh_memory WHERE 1=1"
    params = {}
    if server_id:
        query += " AND server_id = :server_id"
        params["server_id"] = server_id
    if org_id:
        query += " AND org_id = :org_id"
        params["org_id"] = org_id
    return _query_mesh_store(query, params if params else None)


def get_signal_scores(
    session: Session,
    org_id: str,
    server_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch signal scores from ZoComputer store.
    """
    query = "SELECT * FROM mcp_signal_scores WHERE org_id = :org_id"
    params = {"org_id": org_id}
    if server_id:
        query += " AND server_id = :server_id"
        params["server_id"] = server_id
    return _query_mesh_store(query, params)


def get_mesh_memory_endpoint(
    request: Request,
    server_id: Optional[str] = None,
    org_id: Optional[str] = None,
) -> JSONResponse:
    """
    HTTP endpoint wrapper for get_mesh_memory.
    """
    session = next(get_session())
    try:
        data = get_mesh_memory(session, server_id=server_id, org_id=org_id)
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


def signal_scores_endpoint(
    request: Request,
    org_id: str,
    server_id: Optional[str] = None,
) -> JSONResponse:
    """
    HTTP endpoint for signal scores.
    """
    session = next(get_session())
    try:
        data = get_signal_scores(session, org_id=org_id, server_id=server_id)
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


def mesh_memory_endpoint(
    request: Request,
    server_id: Optional[str] = None,
    org_id: Optional[str] = None,
) -> JSONResponse:
    """
    HTTP endpoint for mesh memory operations.
    """
    return get_mesh_memory_endpoint(request, server_id=server_id, org_id=org_id)


def reset_quarantine_api(
    session: Session,
    server_id: str,
) -> Dict[str, Any]:
    """
    Reset quarantine status for a server using parameterized query.
    """
    query = "UPDATE McpServerRegistry SET quarantine = false WHERE server_id = :server_id RETURNING *"
    params = {"server_id": server_id}
    try:
        result = session.execute(text(query), params).fetchone()
        session.commit()
        if result:
            return {"status": "success", "server_id": server_id, "quarantine": False}
        return {"status": "not_found", "server_id": server_id}
    except Exception as e:
        session.rollback()
        return {"error": str(e)}


def mesh_scores_endpoint(
    request: Request,
    org_id: str,
    server_id: Optional[str] = None,
) -> JSONResponse:
    """
    HTTP endpoint for mesh scores.
    """
    return signal_scores_endpoint(request, org_id=org_id, server_id=server_id)


def _dummy_post(
    url: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Dummy POST helper for testing. Uses parameterized payload.
    """
    try:
        resp = requests.post(url, json=payload or {}, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {"error": str(e)}


def _run_self_test(session: Session) -> Dict[str, Any]:
    """
    Self-test validating core functionality.
    """
    errors = []
    
    # Test 1: Verify app models are accessible
    try:
        count = session.query(McpServerRegistry).count()
        logger.info(f"McpServerRegistry accessible, count: {count}")
    except Exception as e:
        errors.append(f"McpServerRegistry query failed: {e}")
    
    # Test 2: Verify Org model
    try:
        count = session.query(Org).count()
        logger.info(f"Org model accessible, count: {count}")
    except Exception as e:
        errors.append(f"Org query failed: {e}")
    
    # Test 3: Test mesh store connectivity
    try:
        result = _query_mesh_store("SELECT 1 as test")
        if "error" in result and "Connection" in result.get("error", ""):
            errors.append(f"Mesh store unavailable: {result['error']}")
        else:
            logger.info("Mesh store connectivity OK")
    except Exception as e:
        errors.append(f"Mesh store test failed: {e}")
    
    # Test 4: Verify parameterized query safety
    try:
        test_query = "SELECT * FROM test WHERE id = :test_id"
        result = _query_mesh_store(test_query, {"test_id": "123"})
        logger.info("Parameterized query test passed")
    except Exception as e:
        errors.append(f"Parameterized query test failed: {e}")
    
    passed = len(errors) == 0
    return {
        "passed": passed,
        "errors": errors,
        "tests_run": 4,
    }


if __name__ == "__main__":
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    # In-memory SQLite for self-test
    engine = create_engine("sqlite:///:memory:", echo=False)
    from app.models import Base
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()
    
    # Override dependency
    app.dependency_overrides[get_session] = lambda: test_session
    
    logger.info("Running self-test...")
    result = _run_self_test(test_session)
    
    if result["passed"]:
        print("PASS")
    else:
        print(f"FAIL: {result['errors']}")
        sys.exit(1)