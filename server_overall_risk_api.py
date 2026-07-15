from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import requests
from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class OverallRiskResponse(BaseModel):
    overall_score: float
    risk_tier: str
    scored_at: str

def get_overall_risk(server_id: str) -> dict:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "sql": """
                SELECT overall_score, risk_tier, scored_at
                FROM mcp_llm_axis_scores
                WHERE server_id = ?
                ORDER BY scored_at DESC
                LIMIT 1
                """,
                "params": [server_id]
            }
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            raise HTTPException(status_code=404, detail="Server not found")
        return data[0]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/servers/{server_id}/overall_risk", response_model=OverallRiskResponse)
async def read_overall_risk(server_id: str):
    result = get_overall_risk(server_id)
    return {
        "overall_score": result["overall_score"],
        "risk_tier": result["risk_tier"],
        "scored_at": result["scored_at"]
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    import requests

    def mock_post(*args, **kwargs):
        return type('MockResponse', (), {
            'json': lambda: [{
                "overall_score": 82.5,
                "risk_tier": "TRUSTED_GENERAL",
                "scored_at": "2026-07-15T12:00:00Z"
            }],
            'raise_for_status': lambda: None
        })()

    requests.post = mock_post

    client = TestClient(app)
    response = client.get("/servers/test_server/overall_risk")
    assert response.status_code == 200
    assert response.json() == {
        "overall_score": 82.5,
        "risk_tier": "TRUSTED_GENERAL",
        "scored_at": "2026-07-15T12:00:00Z"
    }
    print("PASS")