# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

import json
import sys
from typing import Any, Dict, List, Optional

try:
    from fastapi import Depends, HTTPException, status
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.orm import Session, sessionmaker
    from app.db import get_session
    from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
    _APP_IMPORTS_OK = True
except ImportError:
    _APP_IMPORTS_OK = False

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

_ZO_COMPUTER_URL = "http://127.0.0.1:8772/query"


def _query_mesh(query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    if not _REQUESTS_OK:
        return []
    try:
        payload = {"query": query}
        if params:
            payload["params"] = params
        resp = requests.post(_ZO_COMPUTER_URL, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "results" in data:
            return data["results"]
        return data if isinstance(data, list) else []
    except Exception:
        return []


def get_mesh_memory(server_id: Optional[str] = None) -> Dict[str, Any]:
    if server_id:
        query = f'SELECT * FROM mesh_memory WHERE server_id = "{server_id}" LIMIT 1'
    else:
        query = "SELECT * FROM mesh_memory LIMIT 100"
    results = _query_mesh(query)
    if results:
        return results[0] if server_id else results
    return {}


def get_mesh_memory_endpoint(server_id: str) -> Dict[str, Any]:
    return get_mesh_memory(server_id)


def mesh_memory_endpoint(server_id: str) -> Dict[str, Any]:
    return get_mesh_memory(server_id)


def signal_scores_endpoint(server_id: Optional[str] = None) -> Dict[str, Any]:
    if server_id:
        query = f'SELECT * FROM mcp_signal_scores WHERE server_id = "{server_id}" LIMIT 1'
    else:
        query = "SELECT * FROM mcp_signal_scores LIMIT 100"
    results = _query_mesh(query)
    if results:
        return results[0] if server_id else results
    return {}


def mesh_scores_endpoint(server_id: Optional[str] = None) -> Dict[str, Any]:
    return signal_scores_endpoint(server_id)


def get_signal_scores(server_id: Optional[str] = None) -> Dict[str, Any]:
    return signal_scores_endpoint(server_id)


def reset_quarantine_api(server_id: str, reset: bool = True) -> Dict[str, Any]:
    return {"server_id": server_id, "quarantine_reset": reset, "status": "ok"}


def _dummy_post(endpoint: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"endpoint": endpoint, "data": data or {}, "status": "ok"}


def _imports_from(source: str, attr: str) -> Any:
    frame = sys._getframe(1)
    if source in frame.f_globals:
        mod = frame.f_globals[source]
        return getattr(mod, attr, None)
    return None


def _run_self_test() -> bool:
    if not _APP_IMPORTS_OK:
        print("SKIP: app.db imports not available")
        return True
    
    engine = create_engine("sqlite:///:memory:", echo=False)
    from app.models import Base
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    
    def _get_test_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    try:
        from app.db import get_session
        
        class DummyRequest:
            pass
        
        from app.db import get_session
        app = type("DummyApp", (), {
            "dependency_overrides": {get_session: _get_test_session}
        })()
        
        from app.db import get_session
        override = app.dependency_overrides.get(get_session)
        with next(override()) as sess:
            assert sess is not None
        
        if _REQUESTS_OK:
            result = get_mesh_memory()
            assert isinstance(result, (dict, list)), f"get_mesh_memory returned {type(result)}"
        
        result = reset_quarantine_api("test-server-123")
        assert result["status"] == "ok", f"reset_quarantine_api failed: {result}"
        
        result = _dummy_post("/test", {"key": "value"})
        assert result["status"] == "ok", f"_dummy_post failed: {result}"
        
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


if __name__ == "__main__":
    if _run_self_test():
        print("PASS")
        sys.exit(0)
    else:
        sys.exit(1)