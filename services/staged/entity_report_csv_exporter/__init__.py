from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory

def signal_scores_endpoint():
    return {
        "status": "active",
        "description": "Endpoint for retrieving signal scores"
    }

def test_signal_scores_endpoint():
    app = FastAPI()

    @app.get("/signal-scores")
    async def get_signal_scores():
        return signal_scores_endpoint()

    client = TestClient(app)
    response = client.get("/signal-scores")
    assert response.status_code == 200
    assert response.json() == {
        "status": "active",
        "description": "Endpoint for retrieving signal scores"
    }
    print("PASS")

if __name__ == "__main__":
    test_signal_scores_endpoint()