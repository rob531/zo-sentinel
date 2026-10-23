from fastapi import FastAPI, Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import Any, Dict, List
import requests

app = FastAPI()

def get_mcp_servers(db_session=Depends(get_session)) -> List[McpServerRegistry]:
    return db_session.query(McpServerRegistry).all()

def get_llm_axis_scores(db_session=Depends(get_session)) -> List[McpLlmAxisScore]:
    return db_session.query(McpLlmAxisScore).all()

def get_signal_scores() -> List[Dict[str, Any]]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    return response.json()

def get_score_disputes(db_session=Depends(get_session)) -> List[McpScoreDispute]:
    return db_session.query(McpScoreDispute).all()

def get_orgs(db_session=Depends(get_session)) -> List[Org]:
    return db_session.query(Org).all()

def get_users(db_session=Depends(get_session)) -> List[User]:
    return db_session.query(User).all()

if __name__ == "__main__":
    from app.db import get_session as get_test_session
    from app.main import app as _fastapi_app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    _fastapi_app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Test the functions
    try:
        get_mcp_servers()
        get_llm_axis_scores()
        get_signal_scores()
        get_score_disputes()
        get_orgs()
        get_users()
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")