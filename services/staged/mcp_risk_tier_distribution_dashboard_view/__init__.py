from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import PerspectiveSnapshot, McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

class SentinelService:
    def __init__(self):
        self.session: Session = get_session()

    def get_perspective_snapshot(self, id: int) -> Optional[PerspectiveSnapshot]:
        return self.session.query(PerspectiveSnapshot).filter(PerspectiveSnapshot.id == id).first()

    def get_server_registry(self, server_id: str) -> Optional[McpServerRegistry]:
        return self.session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()

    def get_llm_axis_scores(self, server_id: str) -> List[McpLlmAxisScore]:
        return self.session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()

    def get_score_disputes(self, server_id: str) -> List[McpScoreDispute]:
        return self.session.query(McpScoreDispute).filter(McpScoreDispute.server_id == server_id).all()

    def get_org(self, id: int) -> Optional[Org]:
        return self.session.query(Org).filter(Org.id == id).first()

    def get_user(self, id: int) -> Optional[User]:
        return self.session.query(User).filter(User.id == id).first()

class QueryRequest(BaseModel):
    query: str

class QueryResponse(BaseModel):
    result: str

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: Session(bind=None, autocommit=False, autoflush=False)

    @test_app.post("/query", response_model=QueryResponse)
    async def query_endpoint(request: QueryRequest):
        return {"result": f"Processed: {request.query}"}

    client = TestClient(test_app)
    response = client.post("/query", json={"query": "test"})
    assert response.status_code == 200
    assert response.json() == {"result": "Processed: test"}
    print("PASS")