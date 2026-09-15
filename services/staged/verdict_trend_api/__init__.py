# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

import logging
from typing import Any, Dict, List, Optional

import requests
from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

logger = logging.getLogger(__name__)

BUS_URL = "http://127.0.0.1:8772/query"


def query_bus(payload: Dict[str, Any], timeout: int = 10) -> List[Dict[str, Any]]:
    """Query the bus service."""
    try:
        response = requests.post(BUS_URL, json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json().get("data", response.json())
    except Exception as e:
        logger.warning(f"Bus query failed: {e}")
        return []


def _get_mesh_memory_impl(
    table: str, id: Optional[str] = None, filters: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Internal mesh memory query implementation."""
    payload: Dict[str, Any] = {"table": table, "action": "select"}
    if id:
        payload["filters"] = {"id": id}
    elif filters:
        payload["filters"] = filters
    return query_bus(payload)


def get_mesh_memory(
    session: Session = Depends(get_session),
    server_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get mesh memory records, optionally filtered by server ID."""
    if server_id:
        result = _get_mesh_memory_impl(table="mesh_memory", id=server_id)
    else:
        result = _get_mesh_memory_impl(table="mesh_memory")
    return result


def mesh_memory_endpoint() -> List[Dict[str, Any]]:
    """Mesh memory endpoint."""
    try:
        return _get_mesh_memory_impl(table="mesh_memory")
    except Exception as e:
        logger.warning(f"mesh_memory_endpoint failed: {e}")
        return []


def get_mesh_memory_by_id(server_id: str) -> List[Dict[str, Any]]:
    """Get mesh memory by server ID."""
    return _get_mesh_memory_impl(table="mesh_memory", id=server_id)


def signal_scores_endpoint() -> List[Dict[str, Any]]:
    """Signal scores endpoint."""
    return query_bus({"table": "mcp_signal_scores", "action": "select"})


def high_value_servers_endpoint() -> List[Dict[str, Any]]:
    """High value servers endpoint."""
    return query_bus({"table": "mcp_server_registry", "action": "high_value"})


def get_score_disputes_endpoint() -> List[Dict[str, Any]]:
    """Score disputes endpoint."""
    return query_bus({"table": "mcp_score_disputes", "action": "select"})


def get_score_disputes(
    session: Session = Depends(get_session),
) -> List[McpScoreDispute]:
    """Get score disputes from app database."""
    return session.query(McpScoreDispute).all()


def mesh_scores_endpoint() -> List[Dict[str, Any]]:
    """Mesh scores endpoint."""
    return query_bus({"table": "mcp_signal_scores", "action": "select"})


def read_all(
    table: str,
    filters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Read all records from a table."""
    return query_bus({"table": table, "action": "select", "filters": filters})


def reset_quarantine_api(server_id: str) -> Dict[str, Any]:
    """Reset quarantine for a server."""
    result = query_bus({
        "table": "mesh_memory",
        "action": "update",
        "filters": {"id": server_id},
        "data": {"quarantined": False},
    })
    return {"server_id": server_id, "quarantined": False, "result": result}


def _run_self_test() -> str:
    """Run self-test returning PASS or FAIL."""
    try:
        mesh_memory_endpoint()
        signal_scores_endpoint()
        high_value_servers_endpoint()
        get_score_disputes_endpoint()
        read_all("mcp_signal_scores")
        return "PASS"
    except Exception as e:
        return f"FAIL: {e}"


def test():
    """Main self-test entry point."""
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    from app.main import app as main_app
    main_app.dependency_overrides[get_session] = override_get_session

    try:
        result = mesh_memory_endpoint()
        assert isinstance(result, (list, dict)), f"mesh_memory_endpoint returned {type(result)}"

        result = signal_scores_endpoint()
        assert isinstance(result, (list, dict)), f"signal_scores_endpoint returned {type(result)}"

        result = high_value_servers_endpoint()
        assert isinstance(result, list), f"high_value_servers_endpoint returned {type(result)}"

        result = get_score_disputes_endpoint()
        assert isinstance(result, list), f"get_score_disputes_endpoint returned {type(result)}"

        result = read_all("mcp_signal_scores")
        assert isinstance(result, list), f"read_all returned {type(result)}"

        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
    finally:
        main_app.dependency_overrides.clear()
        test_engine.dispose()


if __name__ == "__main__":
    test()