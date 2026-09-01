"""
Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite.
"""

from typing import Any, Dict, List, Optional
import requests
from sqlalchemy import text
from app.db import get_session
from app.models import MCP_signal_scores, mesh_memory

MESH_API_URL = "http://127.0.0.1:8772/query"


def _run_self_test() -> bool:
    """Run self-test to verify the module is working correctly."""
    try:
        requests.post(MESH_API_URL, json={"query": "SELECT 1"}, timeout=5)
        session = next(get_session())
        session.execute(text("SELECT 1"))
        session.close()
        return True
    except Exception:
        return False


def get_mesh_memory(org_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch mesh memory from the ZoComputer store."""
    try:
        payload: Dict[str, Any] = {"query": "SELECT * FROM mesh_memory"}
        if org_id:
            payload["query"] += f" WHERE org_id = '{org_id}'"
        response = requests.post(MESH_API_URL, json=payload, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                return data
            return [data] if data else []
        return []
    except Exception:
        return []


def get_mesh_scores(org_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch mesh scores from app database."""
    session = next(get_session())
    try:
        query = session.query(MCP_signal_scores)
        if org_id:
            query = query.filter(MCP_signal_scores.org_id == org_id)
        results = query.all()
        return [
            {
                "org_id": r.org_id,
                "source": r.source,
                "mesh_score": r.mesh_score,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in results
        ]
    finally:
        session.close()


def get_signal_scores(org_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch signal scores from app database."""
    session = next(get_session())
    try:
        query = session.query(MCP_signal_scores)
        if org_id:
            query = query.filter(MCP_signal_scores.org_id == org_id)
        results = query.all()
        return [
            {
                "org_id": r.org_id,
                "source": r.source,
                "signal_score": getattr(r, "signal_score", None),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in results
        ]
    finally:
        session.close()


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import get_session

    engine = create_engine("sqlite:///:memory:")
    from app.models import Base
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def _test_session():
        yield TestingSession()

    from app import db as app_db_module
    app_db_module.get_session = _test_session

    if _run_self_test():
        print("PASS")
    else:
        print("FAIL")