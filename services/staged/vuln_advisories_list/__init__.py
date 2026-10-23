from typing import List, Dict, Optional
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests

def get_mesh_memory(server_id: int, session: Session = Depends(get_session)) -> Optional[Dict]:
    """Fetch mesh memory for a given server ID from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return response.json().get("data", [{}])[0] if response.json().get("data") else None
    except requests.RequestException:
        return None

def get_mesh_scores(server_id: int, session: Session = Depends(get_session)) -> Optional[List[Dict]]:
    """Fetch mesh scores for a given server ID from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return response.json().get("data", [])
    except requests.RequestException:
        return None

def get_signal_scores(server_id: int, session: Session = Depends(get_session)) -> Optional[List[Dict]]:
    """Fetch signal scores for a given server ID from the ZoComputer store."""
    return get_mesh_scores(server_id)

def setup_database(session: Session = Depends(get_session)) -> None:
    """Ensure database tables exist."""
    session.execute("CREATE TABLE IF NOT EXISTS McpServerRegistry (id INTEGER PRIMARY KEY, name TEXT)")
    session.execute("CREATE TABLE IF NOT EXISTS McpLlmAxisScore (id INTEGER PRIMARY KEY, server_id INTEGER, score REAL)")
    session.execute("CREATE TABLE IF NOT EXISTS McpScoreDispute (id INTEGER PRIMARY KEY, server_id INTEGER, dispute TEXT)")
    session.execute("CREATE TABLE IF NOT EXISTS orgs (id INTEGER PRIMARY KEY, name TEXT)")
    session.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT)")
    session.commit()

def reset_server_export_api_quarantine(session: Session = Depends(get_session)) -> None:
    """Reset server export API quarantine status."""
    session.execute("UPDATE McpServerRegistry SET quarantine = 0")
    session.commit()

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.main import app

    # Override the session for self-test
    test_engine = create_engine("sqlite:///:memory:")
    test_session = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: test_session()

    # Create test tables
    test_session().execute("CREATE TABLE McpServerRegistry (id INTEGER PRIMARY KEY, name TEXT, quarantine INTEGER)")
    test_session().execute("CREATE TABLE McpLlmAxisScore (id INTEGER PRIMARY KEY, server_id INTEGER, score REAL)")
    test_session().execute("CREATE TABLE McpScoreDispute (id INTEGER PRIMARY KEY, server_id INTEGER, dispute TEXT)")
    test_session().execute("CREATE TABLE orgs (id INTEGER PRIMARY KEY, name TEXT)")
    test_session().execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    test_session().commit()

    # Test functions
    setup_database()
    reset_server_export_api_quarantine()

    # Mock mesh memory and scores
    test_session().execute("INSERT INTO McpServerRegistry (id, name) VALUES (1, 'test_server')")
    test_session().commit()

    mesh_memory = get_mesh_memory(1)
    mesh_scores = get_mesh_scores(1)
    signal_scores = get_signal_scores(1)

    if mesh_memory is not None and mesh_scores is not None and signal_scores is not None:
        print("PASS")
    else:
        print("FAIL")