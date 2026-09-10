from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

def get_mesh_memory() -> dict:
    """Fetch mesh memory data from ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mesh_memory"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def reset_quarantine_api() -> bool:
    """Reset quarantine status for all servers."""
    session = Depends(get_session)
    try:
        session.query(McpServerRegistry).update({"quarantined": False}, synchronize_session=False)
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

def get_signal_scores(server_id: int) -> dict:
    """Get signal scores for a specific server."""
    session = Depends(get_session)
    try:
        scores = session.query(McpLlmAxisScore).filter_by(server_id=server_id).first()
        if not scores:
            raise HTTPException(status_code=404, detail="Server not found")
        return {
            "server_id": scores.server_id,
            "scores": {
                "risk": scores.risk_score,
                "performance": scores.performance_score,
                "security": scores.security_score
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

def _run_self_test() -> str:
    """Self-test for the module."""
    try:
        # Test get_mesh_memory
        mesh_memory = get_mesh_memory()
        if not isinstance(mesh_memory, dict):
            raise ValueError("get_mesh_memory did not return a dict")

        # Test reset_quarantine_api
        reset_quarantine_api()

        # Test get_signal_scores
        test_server_id = 1
        signal_scores = get_signal_scores(test_server_id)
        if not isinstance(signal_scores, dict):
            raise ValueError("get_signal_scores did not return a dict")

        return "PASS"
    except Exception as e:
        return f"FAIL: {str(e)}"

if __name__ == "__main__":
    print(_run_self_test())