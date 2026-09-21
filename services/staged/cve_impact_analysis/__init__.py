from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory
from typing import List, Optional
import requests

def signal_scores_endpoint():
    app = FastAPI()

    @app.get("/signal_scores", response_model=List[dict])
    async def get_signal_scores(
        server_id: Optional[int] = None,
        db: Session = Depends(get_session)
    ):
        query = db.query(McpLlmAxisScore)
        if server_id is not None:
            query = query.filter(McpLlmAxisScore.server_id == server_id)
        scores = query.all()
        return [{"id": score.id, "server_id": score.server_id, "axis": score.axis, "score": score.score} for score in scores]

    return app

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Setup in-memory test database
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(engine)

    # Override the dependency to use the test database
    def override_get_session() -> Session:
        return Session(engine)

    test_app = signal_scores_endpoint()
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    # Test the endpoint
    response = client.get("/signal_scores")
    assert response.status_code == 200
    print("PASS")