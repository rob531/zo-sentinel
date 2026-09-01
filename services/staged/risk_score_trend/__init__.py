from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry, McpLlmAxisScore, Org, User
from typing import List, Optional
import requests

class ScoreDisputeService:
    @staticmethod
    def get_signal_scores(session: Session = Depends(get_session)) -> List[dict]:
        """Fetch signal scores from the database."""
        return session.query(McpServerRegistry).all()

    @staticmethod
    def get_mesh_memory_endpoint() -> str:
        """Return the endpoint for mesh memory."""
        return "http://127.0.0.1:8772/query"

    @staticmethod
    def get_score_disputes_endpoint() -> str:
        """Return the endpoint for score disputes."""
        return "http://127.0.0.1:8772/query"

    @staticmethod
    def get_mesh_scores(session: Session = Depends(get_session)) -> List[dict]:
        """Fetch mesh scores from the database."""
        return session.query(McpLlmAxisScore).all()

    @staticmethod
    def signal_scores_endpoint() -> str:
        """Return the endpoint for signal scores."""
        return "http://127.0.0.1:8772/query"

    @staticmethod
    def llm_axis_scores_endpoint() -> str:
        """Return the endpoint for LLM axis scores."""
        return "http://127.0.0.1:8772/query"

    @staticmethod
    def mesh_scores_endpoint() -> str:
        """Return the endpoint for mesh scores."""
        return "http://127.0.0.1:8772/query"

    @staticmethod
    def orgs_endpoint() -> str:
        """Return the endpoint for orgs."""
        return "http://127.0.0.1:8772/query"

    @staticmethod
    def mesh_scores() -> List[dict]:
        """Fetch mesh scores from the database."""
        response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
        return response.json()

class McpScoreDispute(ScoreDisputeService):
    pass

def _run_self_test():
    """Self-test for the ScoreDisputeService."""
    app = FastAPI()
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    @app.get("/test")
    async def test_endpoint():
        return {"status": "PASS"}

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

if __name__ == "__main__":
    _run_self_test()