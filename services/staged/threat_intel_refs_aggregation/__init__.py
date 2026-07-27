from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Dict, Optional
import requests

def get_mesh_scores(server_id: int, session: Session = Depends(get_session)) -> Dict[str, float]:
    """Fetch mesh scores for a given server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
    )
    if response.status_code != 200:
        raise Exception("Failed to fetch mesh scores")
    return response.json()

def get_mesh_memory(server_id: int, session: Session = Depends(get_session)) -> Dict[str, str]:
    """Fetch mesh memory for a given server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"}
    )
    if response.status_code != 200:
        raise Exception("Failed to fetch mesh memory")
    return response.json()

def get_signal_scores(server_id: int, session: Session = Depends(get_session)) -> Dict[str, float]:
    """Fetch signal scores for a given server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
    )
    if response.status_code != 200:
        raise Exception("Failed to fetch signal scores")
    return response.json()

def get_server_registry(session: Session = Depends(get_session)) -> List[McpServerRegistry]:
    """Fetch all servers from the app database."""
    return session.query(McpServerRegistry).all()

def get_llm_axis_scores(server_id: int, session: Session = Depends(get_session)) -> List[McpLlmAxisScore]:
    """Fetch LLM axis scores for a given server from the app database."""
    return session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()

def get_score_disputes(server_id: int, session: Session = Depends(get_session)) -> List[McpScoreDispute]:
    """Fetch score disputes for a given server from the app database."""
    return session.query(McpScoreDispute).filter(McpScoreDispute.server_id == server_id).all()

def get_org(org_id: int, session: Session = Depends(get_session)) -> Optional[Org]:
    """Fetch an organization from the app database."""
    return session.query(Org).filter(Org.id == org_id).first()

def get_user(user_id: int, session: Session = Depends(get_session)) -> Optional[User]:
    """Fetch a user from the app database."""
    return session.query(User).filter(User.id == user_id).first()

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for self-test
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    # Test functions
    try:
        # Test get_server_registry
        servers = get_server_registry()
        assert isinstance(servers, list)

        # Test get_llm_axis_scores
        llm_scores = get_llm_axis_scores(1)
        assert isinstance(llm_scores, list)

        # Test get_score_disputes
        disputes = get_score_disputes(1)
        assert isinstance(disputes, list)

        # Test get_org
        org = get_org(1)
        assert org is None or isinstance(org, Org)

        # Test get_user
        user = get_user(1)
        assert user is None or isinstance(user, User)

        # Test get_mesh_scores
        mesh_scores = get_mesh_scores(1)
        assert isinstance(mesh_scores, dict)

        # Test get_mesh_memory
        mesh_memory = get_mesh_memory(1)
        assert isinstance(mesh_memory, dict)

        # Test get_signal_scores
        signal_scores = get_signal_scores(1)
        assert isinstance(signal_scores, dict)

        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")