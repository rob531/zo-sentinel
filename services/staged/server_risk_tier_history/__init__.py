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

def query_mesh_data(endpoint: str, params: dict = None) -> dict:
    response = requests.post("http://127.0.0.1:8772/query", json={"endpoint": endpoint, "params": params})
    return response.json()

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Test data access
    test_server = McpServerRegistry(server_id="test", endpoint="http://test.com")
    test_llm = McpLlmAxisScore(score_id="test", axis="test", value=0.5)
    test_dispute = McpScoreDispute(dispute_id="test", score_id="test", reason="test")
    test_org = Org(org_id="test", name="Test Org")
    test_user = User(user_id="test", name="Test User")

    session = next(override_get_session())
    session.add_all([test_server, test_llm, test_dispute, test_org, test_user])
    session.commit()

    # Verify data access
    assert len(get_mcp_servers()) == 1
    assert len(get_llm_axis_scores()) == 1
    assert len(get_score_disputes()) == 1
    assert len(get_orgs()) == 1
    assert len(get_users()) == 1

    # Test mesh data access
    mesh_response = query_mesh_data("mcp_signal_scores")
    assert isinstance(mesh_response, dict)

    print("PASS")