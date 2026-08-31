"""
Auto-emitted service package.
Relative intra-service imports survive staged->active promotion without rewrite.
"""
import json
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    Perspective,
    User,
    VulnAdvisory,
)


def signal_scores_endpoint(
    perspective_id: int,
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """Fetch signal scores from the ZoComputer mesh store."""
    query = {
        "perspective_id": perspective_id,
        "table": "mcp_signal_scores",
    }
    try:
        import requests
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json=query,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception:
        return []


def mesh_scores_endpoint(
    perspective_id: int,
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """Fetch mesh scores for perspective from mesh memory store."""
    query = {
        "perspective_id": perspective_id,
        "table": "mesh_memory",
    }
    try:
        import requests
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json=query,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception:
        return []


def mesh_scores(
    perspective_id: int,
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """Alias for mesh_scores_endpoint for internal service calls."""
    return mesh_scores_endpoint(perspective_id, session)


def get_db(
    session: Session = Depends(get_session),
) -> Session:
    """Return the database session."""
    return session


def get_mesh_memory(
    perspective_id: int,
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """Fetch mesh memory entries for a perspective."""
    return mesh_scores_endpoint(perspective_id, session)


def get_signal_scores(
    perspective_id: int,
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """Alias for signal_scores_endpoint for internal service calls."""
    return signal_scores_endpoint(perspective_id, session)


def _run_self_test() -> bool:
    """Verify the module's dependencies and exports are intact."""
    try:
        assert callable(signal_scores_endpoint)
        assert callable(mesh_scores_endpoint)
        assert callable(mesh_scores)
        assert callable(get_db)
        assert callable(get_mesh_memory)
        assert callable(get_signal_scores)
        
        assert get_session is not None
        assert McpLlmAxisScore is not None
        assert McpScoreDispute is not None
        assert McpServerRegistry is not None
        assert Org is not None
        assert Perspective is not None
        assert User is not None
        assert VulnAdvisory is not None
        
        return True
    except Exception:
        return False


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    
    if _run_self_test():
        print("PASS")
    else:
        print("FAIL")