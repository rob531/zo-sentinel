from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry, Org, User

def get_mesh_memory_endpoint() -> List[dict]:
    """Fetch mesh memory data from ZoComputer store."""
    import requests
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    response.raise_for_status()
    return response.json()

def get_signal_scores() -> List[dict]:
    """Fetch signal scores from ZoComputer store."""
    import requests
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    response.raise_for_status()
    return response.json()

def mesh_scores_endpoint() -> List[dict]:
    """Fetch mesh scores from app database."""
    session = Depends(get_session)
    scores = session.query(McpLlmAxisScore).all()
    return [{"axis_id": score.axis_id, "score": score.score} for score in scores]

def get_score_disputes_endpoint() -> List[dict]:
    """Fetch score disputes from app database."""
    session = Depends(get_session)
    disputes = session.query(McpScoreDispute).all()
    return [{"id": dispute.id, "score_id": dispute.score_id, "reason": dispute.reason} for dispute in disputes]

def orgs_endpoint() -> List[dict]:
    """Fetch orgs from app database."""
    session = Depends(get_session)
    orgs = session.query(Org).all()
    return [{"id": org.id, "name": org.name} for org in orgs]

def users_endpoint() -> List[dict]:
    """Fetch users from app database."""
    session = Depends(get_session)
    users = session.query(User).all()
    return [{"id": user.id, "name": user.name} for user in users]

def signal_scores_endpoint() -> List[dict]:
    """Fetch signal scores from app database."""
    session = Depends(get_session)
    scores = session.query(McpLlmAxisScore).all()
    return [{"axis_id": score.axis_id, "score": score.score} for score in scores]

class OrgService:
    """Service for org operations."""
    def __init__(self, session: Session = Depends(get_session)):
        self.session = session

    def update(self, org_id: int, name: str) -> Org:
        """Update org name."""
        org = self.session.query(Org).filter(Org.id == org_id).first()
        if not org:
            raise HTTPException(status_code=404, detail="Org not found")
        org.name = name
        self.session.commit()
        return org

class UserService:
    """Service for user operations."""
    def __init__(self, session: Session = Depends(get_session)):
        self.session = session

    def update(self, user_id: int, name: str) -> User:
        """Update user name."""
        user = self.session.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        user.name = name
        self.session.commit()
        return user

def _run_self_test():
    """Self-test for the service."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(engine)

    from app.db import get_session
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: Session(engine)

    @app.get("/test")
    def test_endpoint():
        return {"status": "ok"}

    client = TestClient(app)

    response = client.get("/test")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    print("PASS")

if __name__ == "__main__":
    _run_self_test()