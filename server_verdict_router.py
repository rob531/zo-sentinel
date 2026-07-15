from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List
import requests
from app.db import get_session
from app.models import MCPLLMAxisScore

router = APIRouter()

class AxisScore(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float

class ServerVerdict(BaseModel):
    axes: Dict[str, AxisScore]
    overall_risk: float
    risk_tier: str
    criteria_version: str

AXES = [
    "security",
    "privacy",
    "reliability",
    "performance",
    "compliance",
    "maintainability",
    "scalability"
]

def get_server_verdict(server_id: str) -> ServerVerdict:
    # Query write_service for axis scores
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "query": f"SELECT * FROM mcp_llm_axis_scores WHERE server_id = '{server_id}' AND axis_name IN {AXES}"
        }
    )
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch axis scores")

    axis_scores = response.json()

    # Process axis scores
    axes = {}
    for axis in AXES:
        axis_data = next((item for item in axis_scores if item["axis_name"] == axis), None)
        if axis_data:
            axes[axis] = AxisScore(
                label=axis_data["label"],
                p_top=axis_data["p_top"],
                p_critical=axis_data["p_critical"],
                p_danger=axis_data["p_danger"]
            )
        else:
            axes[axis] = AxisScore(
                label="unknown",
                p_top=0.0,
                p_critical=0.0,
                p_danger=0.0
            )

    # Calculate overall risk (average of p_critical)
    overall_risk = sum(axis.p_critical for axis in axes.values()) / len(axes)

    # Determine risk tier
    high_risk = any(axis.p_critical > 0.8 for axis in axes.values())
    risk_tier = "HIGH_RISK_ISOLATED" if high_risk else "LOW_RISK"

    # Get criteria version (mock for now)
    criteria_version = "v1.0"

    return ServerVerdict(
        axes=axes,
        overall_risk=overall_risk,
        risk_tier=risk_tier,
        criteria_version=criteria_version
    )

@router.get("/servers/{server_id}/verdict", response_model=ServerVerdict)
async def server_verdict(server_id: str):
    return get_server_verdict(server_id)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    import requests

    # Monkey-patch requests.post for testing
    def mock_post(*args, **kwargs):
        if "http://127.0.0.1:8772/query" in args[0]:
            return type('Response', (), {
                'status_code': 200,
                'json': lambda: [
                    {
                        "server_id": "test-server",
                        "axis_name": "security",
                        "label": "high",
                        "p_top": 0.9,
                        "p_critical": 0.85,
                        "p_danger": 0.1
                    },
                    {
                        "server_id": "test-server",
                        "axis_name": "privacy",
                        "label": "medium",
                        "p_top": 0.7,
                        "p_critical": 0.3,
                        "p_danger": 0.05
                    }
                ]
            })()
        raise Exception("Unexpected request")

    requests.post = mock_post

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/test-server/verdict")
    assert response.status_code == 200
    data = response.json()

    # Verify all axes are present
    assert set(data["axes"].keys()) == set(AXES)

    # Verify risk tier based on test condition
    assert data["risk_tier"] == "HIGH_RISK_ISOLATED"

    print("PASS")