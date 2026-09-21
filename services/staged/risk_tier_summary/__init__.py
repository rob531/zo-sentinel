from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from pydantic import BaseModel

app = FastAPI()

class ServerRegistry(BaseModel):
    id: int
    server_name: str
    server_url: str
    org_id: int
    is_active: bool

class LLMAxisScores(BaseModel):
    id: int
    server_id: int
    axis_name: str
    score: float
    timestamp: str

class ScoreDisputes(BaseModel):
    id: int
    server_id: int
    user_id: int
    axis_name: str
    disputed_score: float
    new_score: float
    reason: str
    status: str

class OrgModel(BaseModel):
    id: int
    name: str
    description: Optional[str] = None

class UserModel(BaseModel):
    id: int
    username: str
    email: str
    org_id: int

def get_mesh_memory(db: Session = Depends(get_session)):
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

def get_mesh_scores(db: Session = Depends(get_session)):
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mcp_signal_scores"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_signal_scores(db: Session = Depends(get_session)):
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mcp_signal_scores"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def setup_database(db: Session = Depends(get_session)):
    try:
        # Create tables if they don't exist
        db.execute("CREATE TABLE IF NOT EXISTS McpServerRegistry (id SERIAL PRIMARY KEY, server_name TEXT, server_url TEXT, org_id INTEGER, is_active BOOLEAN)")
        db.execute("CREATE TABLE IF NOT EXISTS McpLlmAxisScore (id SERIAL PRIMARY KEY, server_id INTEGER, axis_name TEXT, score FLOAT, timestamp TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS McpScoreDispute (id SERIAL PRIMARY KEY, server_id INTEGER, user_id INTEGER, axis_name TEXT, disputed_score FLOAT, new_score FLOAT, reason TEXT, status TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS orgs (id SERIAL PRIMARY KEY, name TEXT, description TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username TEXT, email TEXT, org_id INTEGER)")
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the dependency for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    # Test setup_database
    setup_database()

    # Test get_mesh_memory
    try:
        get_mesh_memory()
        print("PASS")
    except:
        print("FAIL")