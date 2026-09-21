from datetime import datetime, timedelta
from typing import List, Dict, Optional
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore

def calculate_freshness_score(last_updated: datetime) -> float:
    now = datetime.utcnow()
    time_elapsed = now - last_updated
    days_elapsed = time_elapsed.days
    if days_elapsed < 1:
        return 1.0
    elif days_elapsed < 7:
        return 0.8
    elif days_elapsed < 30:
        return 0.6
    elif days_elapsed < 90:
        return 0.4
    else:
        return 0.2

def get_axis_score_freshness(server_id: str, db: Session = Depends(get_session)) -> Dict[str, float]:
    axis_scores = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()
    freshness_scores = {}
    for score in axis_scores:
        freshness_scores[score.axis_name] = calculate_freshness_score(score.scored_at)
    return freshness_scores

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    app = FastAPI()

    @app.get("/api/axis-score-freshness/{server_id}")
    def test_get_axis_score_freshness(server_id: str, db: Session = Depends(get_session)):
        return get_axis_score_freshness(server_id, db)

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    def test_endpoint():
        response = client.get("/api/axis-score-freshness/test_server")
        assert response.status_code == 200
        assert response.json() == {}

    print("PASS")