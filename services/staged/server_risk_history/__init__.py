from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests

app = FastAPI()

def get_mcp_server_registry(db: Session = Depends(get_session)):
    return db.query(McpServerRegistry).all()

def get_mcp_llm_axis_scores(db: Session = Depends(get_session)):
    return db.query(McpLlmAxisScore).all()

def get_mcp_score_disputes(db: Session = Depends(get_session)):
    return db.query(McpScoreDispute).all()

def get_orgs(db: Session = Depends(get_session)):
    return db.query(Org).all()

def get_users(db: Session = Depends(get_session)):
    return db.query(User).all()

def query_mesh_data(endpoint: str, timeout: int = 10):
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": endpoint},
            timeout=timeout
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override for self-test
    test_engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create tables for self-test
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Test functions
    try:
        get_mcp_server_registry()
        get_mcp_llm_axis_scores()
        get_mcp_score_disputes()
        get_orgs()
        get_users()
        query_mesh_data("SELECT * FROM mcp_signal_scores LIMIT 1")
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")