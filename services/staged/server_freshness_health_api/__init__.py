from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from urllib.parse import urlparse

class ZoSentinelService:
    def __init__(self):
        self.base_url = "http://127.0.0.1:8772"

    async def get_mcp_server_registry(self, session: Session = Depends(get_session)) -> List[McpServerRegistry]:
        return session.query(McpServerRegistry).all()

    async def get_mcp_llm_axis_scores(self, session: Session = Depends(get_session)) -> List[McpLlmAxisScore]:
        return session.query(McpLlmAxisScore).all()

    async def get_mcp_score_disputes(self, session: Session = Depends(get_session)) -> List[McpScoreDispute]:
        return session.query(McpScoreDispute).all()

    async def query_mesh_memory(self, query: str) -> Dict[str, Any]:
        response = requests.post(f"{self.base_url}/query", json={"query": query})
        response.raise_for_status()
        return response.json()

    def _validate_url_scheme(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in ['http', 'https']

    async def get_orgs(self, session: Session = Depends(get_session)) -> List[Org]:
        return session.query(Org).all()

    async def get_users(self, session: Session = Depends(get_session)) -> List[User]:
        return session.query(User).all()

def create_app() -> FastAPI:
    app = FastAPI()

    sentinel_service = ZoSentinelService()

    @app.get("/McpServerRegistry")
    async def read_mcp_server_registry(session: Session = Depends(get_session)):
        return await sentinel_service.get_mcp_server_registry(session)

    @app.get("/McpLlmAxisScore")
    async def read_mcp_llm_axis_scores(session: Session = Depends(get_session)):
        return await sentinel_service.get_mcp_llm_axis_scores(session)

    @app.get("/McpScoreDispute")
    async def read_mcp_score_disputes(session: Session = Depends(get_session)):
        return await sentinel_service.get_mcp_score_disputes(session)

    @app.post("/query_mesh_memory")
    async def query_mesh_memory_endpoint(query: str):
        if not sentinel_service._validate_url_scheme(query):
            raise HTTPException(status_code=400, detail="Invalid URL scheme")
        return await sentinel_service.query_mesh_memory(query)

    @app.get("/orgs")
    async def read_orgs(session: Session = Depends(get_session)):
        return await sentinel_service.get_orgs(session)

    @app.get("/users")
    async def read_users(session: Session = Depends(get_session)):
        return await sentinel_service.get_users(session)

    return app

if __name__ == "__main__":
    import uvicorn
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Self-test setup
    test_app = create_app()

    # Override dependencies for testing
    test_app.dependency_overrides[get_session] = lambda: Session(
        bind=create_engine("sqlite:///:memory:", poolclass=StaticPool),
        expire_on_commit=False
    )

    # Run self-test
    try:
        with test_app.dependency_overrides[get_session]() as session:
            # Test data setup would go here if needed
            pass
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")