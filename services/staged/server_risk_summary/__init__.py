from fastapi import FastAPI, Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List
import requests

app = FastAPI()

def get_mcp_servers(session=Depends(get_session)) -> List[McpServerRegistry]:
    return session.query(McpServerRegistry).all()

def get_llm_axis_scores(session=Depends(get_session)) -> List[McpLlmAxisScore]:
    return session.query(McpLlmAxisScore).all()

def get_score_disputes(session=Depends(get_session)) -> List[McpScoreDispute]:
    return session.query(McpScoreDispute).all()

def get_orgs(session=Depends(get_session)) -> List[Org]:
    return session.query(Org).all()

def get_users(session=Depends(get_session)) -> List[User]:
    return session.query(User).all()

def query_mesh_memory(endpoint: str, query: str) -> dict:
    try:
        response = requests.post(
            f"http://127.0.0.1:8772/query/{endpoint}",
            json={"query": query},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise Exception(f"Error querying mesh memory: {e}")

if __name__ == "__main__":
    from app.db import get_session
    from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create tables
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    McpScoreDispute.__table__.create(engine)
    Org.__table__.create(engine)
    User.__table__.create(engine)

    # Test functions
    try:
        get_mcp_servers()
        get_llm_axis_scores()
        get_score_disputes()
        get_orgs()
        get_users()
        query_mesh_memory("mcp_signal_scores", "SELECT * FROM mcp_signal_scores")
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")