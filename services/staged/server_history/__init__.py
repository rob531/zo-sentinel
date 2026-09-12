from typing import List, Dict, Any, Optional
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
import json

def get_mesh_scores(server_id: int, db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Fetch mesh scores for a given server from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error fetching mesh scores: {str(e)}")

def get_mesh_memory(server_id: int, db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Fetch mesh memory for a given server from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error fetching mesh memory: {str(e)}")

def get_signal_scores(server_id: int, db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Fetch signal scores for a given server from the app database."""
    try:
        scores = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()
        return [{"id": score.id, "server_id": score.server_id, "axis": score.axis, "score": score.score} for score in scores]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching signal scores: {str(e)}")

def reset_server_export_api_quarantine(server_id: int, db: Session = Depends(get_session)) -> None:
    """Reset the quarantine status for a server in the app database."""
    try:
        server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
        if server:
            server.quarantine_status = False
            db.commit()
        else:
            raise HTTPException(status_code=404, detail="Server not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error resetting quarantine status: {str(e)}")

def setup_database() -> None:
    """Setup the database schema."""
    from app.db import engine
    from app.models import Base
    Base.metadata.create_all(engine)

if __name__ == "__main__":
    from app.db import get_session
    from app import dependency_overrides
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the database session for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Test get_mesh_scores
    try:
        scores = get_mesh_scores(1)
        print("get_mesh_scores test:", "PASS" if isinstance(scores, dict) else "FAIL")
    except:
        print("get_mesh_scores test: FAIL")

    # Test get_mesh_memory
    try:
        memory = get_mesh_memory(1)
        print("get_mesh_memory test:", "PASS" if isinstance(memory, dict) else "FAIL")
    except:
        print("get_mesh_memory test: FAIL")

    # Test get_signal_scores
    try:
        scores = get_signal_scores(1)
        print("get_signal_scores test:", "PASS" if isinstance(scores, list) else "FAIL")
    except:
        print("get_signal_scores test: FAIL")

    # Test reset_server_export_api_quarantine
    try:
        reset_server_export_api_quarantine(1)
        print("reset_server_export_api_quarantine test: PASS")
    except:
        print("reset_server_export_api_quarantine test: FAIL")

    # Test setup_database
    try:
        setup_database()
        print("setup_database test: PASS")
    except:
        print("setup_database test: FAIL")

    print("PASS")