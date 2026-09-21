from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
import requests
from urllib.parse import urlparse

def get_mesh_data(query: str) -> Optional[List[dict]]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": query},
            timeout=5
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None

def validate_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ('http', 'https')

class PerspectiveSnapshot:
    def __init__(self, id: int, member: str, score: float, timestamp: str):
        self.id = id
        self.member = member
        self.score = score
        self.timestamp = timestamp

def get_perspective_snapshot(member: str, session: Session = Depends(get_session)) -> Optional[PerspectiveSnapshot]:
    query = f"""
    SELECT id, member, score, timestamp
    FROM mcp_signal_scores
    WHERE member = '{member}'
    ORDER BY timestamp DESC
    LIMIT 1
    """
    data = get_mesh_data(query)
    if not data:
        return None
    return PerspectiveSnapshot(**data[0])

def get_server_registries(session: Session = Depends(get_session)) -> List[McpServerRegistry]:
    return session.query(McpServerRegistry).all()

def get_llm_axis_scores(session: Session = Depends(get_session)) -> List[McpLlmAxisScore]:
    return session.query(McpLlmAxisScore).all()

def get_score_disputes(session: Session = Depends(get_session)) -> List[McpScoreDispute]:
    return session.query(McpScoreDispute).all()

def get_orgs(session: Session = Depends(get_session)) -> List[Org]:
    return session.query(Org).all()

def get_users(session: Session = Depends(get_session)) -> List[User]:
    return session.query(User).all()

if __name__ == "__main__":
    test_app = FastAPI()

    @test_app.get("/test")
    async def test_endpoint():
        return {"status": "PASS"}

    import uvicorn
    uvicorn.run(test_app, host="127.0.0.1", port=8000)