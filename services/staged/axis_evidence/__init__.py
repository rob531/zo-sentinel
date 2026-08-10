from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
import requests
from pydantic import BaseModel

app = FastAPI()

class MeshMemory(BaseModel):
    server_id: int
    memory: str

class SignalScores(BaseModel):
    server_id: int
    scores: dict

class MeshScores(BaseModel):
    server_id: int
    scores: dict

def get_mesh_memory(server_id: int, db: Session = Depends(get_session)) -> Optional[str]:
    """Retrieve mesh memory for a given server_id from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT memory FROM mesh_memory WHERE server_id = {server_id}"}
    )
    if response.status_code == 200:
        result = response.json()
        if result and 'memory' in result[0]:
            return result[0]['memory']
    return None

def get_signal_scores(server_id: int, db: Session = Depends(get_session)) -> Optional[dict]:
    """Retrieve signal scores for a given server_id from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT scores FROM mcp_signal_scores WHERE server_id = {server_id}"}
    )
    if response.status_code == 200:
        result = response.json()
        if result and 'scores' in result[0]:
            return result[0]['scores']
    return None

def get_mesh_scores(server_id: int, db: Session = Depends(get_session)) -> Optional[dict]:
    """Retrieve mesh scores for a given server_id from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT scores FROM mesh_scores WHERE server_id = {server_id}"}
    )
    if response.status_code == 200:
        result = response.json()
        if result and 'scores' in result[0]:
            return result[0]['scores']
    return None

def setup_database(db: Session = Depends(get_session)) -> None:
    """Ensure database tables are created."""
    db.execute("CREATE TABLE IF NOT EXISTS McpServerRegistry (id SERIAL PRIMARY KEY, name TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS McpLlmAxisScore (id SERIAL PRIMARY KEY, server_id INTEGER, axis TEXT, score REAL)")
    db.execute("CREATE TABLE IF NOT EXISTS McpScoreDispute (id SERIAL PRIMARY KEY, server_id INTEGER, dispute TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS orgs (id SERIAL PRIMARY KEY, name TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, name TEXT, org_id INTEGER)")

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.dependency_overrides import dependency_overrides

    # Override the session for self-test
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    dependency_overrides[get_session] = lambda: SessionLocal()

    # Test setup_database
    setup_database()

    # Test get_mesh_memory
    assert get_mesh_memory(1) is None

    # Test get_signal_scores
    assert get_signal_scores(1) is None

    # Test get_mesh_scores
    assert get_mesh_scores(1) is None

    print("PASS")