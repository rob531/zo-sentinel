from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from typing import List, Optional

app = FastAPI()

def get_mcp_servers(db: Session = Depends(get_session)):
    return db.query(McpServerRegistry).all()

def get_llm_scores(db: Session = Depends(get_session)):
    return db.query(McpLlmAxisScore).all()

def get_score_disputes(db: Session = Depends(get_session)):
    return db.query(McpScoreDispute).all()

def get_orgs(db: Session = Depends(get_session)):
    return db.query(Org).all()

def get_users(db: Session = Depends(get_session)):
    return db.query(User).all()

def query_mesh_memory(endpoint: str, params: Optional[dict] = None):
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"endpoint": endpoint, "params": params},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_signal_scores():
    return query_mesh_memory("mcp_signal_scores")

def get_mesh_memory():
    return query_mesh_memory("mesh_memory")

if __name__ == "__main__":
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the dependency for self-test
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Create tables for the test
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Test functions
    try:
        get_mcp_servers()
        get_llm_scores()
        get_score_disputes()
        get_orgs()
        get_users()
        get_signal_scores()
        get_mesh_memory()
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")