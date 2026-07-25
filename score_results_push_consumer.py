from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime
import requests
from typing import List, Dict
from app.db import get_session
from app.models import MCPLLMAxisScore

router = APIRouter()

def push_scores(server_id: str) -> Dict:
    session: Session = Depends(get_session)()
    try:
        scores: List[MCPLLMAxisScore] = session.query(MCPLLMAxisScore).filter(
            MCPLLMAxisScore.server_id == server_id
        ).all()

        if not scores:
            return {"error": "No scores found for server_id"}

        overall_score = sum(score.p_top for score in scores) / len(scores)
        timestamp = datetime.utcnow().isoformat()

        payload = {
            "server_id": server_id,
            "overall_score": overall_score,
            "timestamp": timestamp
        }

        response = requests.post(
            "http://127.0.0.1:8773/monitor/score_push",
            json=payload
        )
        response.raise_for_status()

        return payload
    finally:
        session.close()

if __name__ == "__main__":
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.dependency_overrides import dependency_overrides

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    dependency_overrides[get_session] = override_get_session

    from app.models import MCPLLMAxisScore

    mock_scores = [
        MCPLLMAxisScore(
            server_id="test-server",
            axis_name="axis1",
            p_top=80,
            scored_at=datetime.utcnow()
        ),
        MCPLLMAxisScore(
            server_id="test-server",
            axis_name="axis2",
            p_top=90,
            scored_at=datetime.utcnow()
        )
    ]

    session = SessionLocal()
    session.add_all(mock_scores)
    session.commit()

    def mock_post(url, json):
        assert url == "http://127.0.0.1:8773/monitor/score_push"
        assert 0 <= json["overall_score"] <= 100
        return requests.Response()

    requests.post = mock_post

    result = push_scores("test-server")
    assert 0 <= result["overall_score"] <= 100
    print("PASS")