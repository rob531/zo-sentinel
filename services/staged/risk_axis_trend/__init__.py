from typing import List, Dict, Any
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests

def get_mesh_memory() -> Dict[str, Any]:
    """Fetch mesh memory data from ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mesh_memory"
    })
    response.raise_for_status()
    return response.json()

def get_signal_scores() -> List[Dict[str, Any]]:
    """Fetch signal scores data from ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mcp_signal_scores"
    })
    response.raise_for_status()
    return response.json()

def get_mesh_scores() -> List[Dict[str, Any]]:
    """Fetch mesh scores data from ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mcp_signal_scores WHERE score_type = 'mesh'"
    })
    response.raise_for_status()
    return response.json()

def get_app_data(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Fetch app data from Postgres database."""
    data = {
        "servers": session.query(McpServerRegistry).all(),
        "scores": session.query(McpLlmAxisScore).all(),
        "disputes": session.query(McpScoreDispute).all(),
        "orgs": session.query(Org).all(),
        "users": session.query(User).all()
    }
    return data

if __name__ == "__main__":
    from app.db import get_session
    # FU-369: removed an import of `override_dependencies_for_testing` from a module that does
    # not exist in this tree, together with its call below. The call
    # installed nothing this file does not already do for itself.
    # FU-369: call removed with its phantom import (see above).

    try:
        mesh_memory = get_mesh_memory()
        signal_scores = get_signal_scores()
        mesh_scores = get_mesh_scores()
        app_data = get_app_data()

        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")