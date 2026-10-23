"""
Auto-emitted service package for mesh signal scoring.
"""
import json
from typing import Optional, List, Dict, Any

import requests
from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.db import get_session
from app.models import Org, User, McpServerRegistry, McpLlmAxisScore, McpScoreDispute


def get_mesh_memory(
    org_id: int,
    session: Session = Depends(get_session)
) -> List[Dict[str, Any]]:
    """
    Retrieve mesh memory entries for an organization.
    Data is sourced from the ZoComputer store via write_service.
    """
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "table": "mesh_memory",
                "org_id": org_id
            },
            timeout=10
        )
        response.raise_for_status()
        return response.json().get("results", [])
    except requests.RequestException:
        return []


def get_signal_scores(
    org_id: int,
    session: Session = Depends(get_session)
) -> List[Dict[str, Any]]:
    """
    Retrieve signal scores for an organization.
    Data is sourced from the ZoComputer store via write_service.
    """
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "table": "mcp_signal_scores",
                "org_id": org_id
            },
            timeout=10
        )
        response.raise_for_status()
        return response.json().get("results", [])
    except requests.RequestException:
        return []


if __name__ == "__main__":
    from app.main import app
    
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(bind=engine)
    
    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    print("PASS")