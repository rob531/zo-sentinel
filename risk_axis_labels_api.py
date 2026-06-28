from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List
import httpx
import json
from fastapi.testclient import TestClient

router = APIRouter()

class RiskAxisLabelsResponse(BaseModel):
    risk_axes: Dict[str, List[str]]

def get_write_service_client():
    return httpx.AsyncClient(base_url="http://127.0.0.1:8772")

async def query_axis_labels(client: httpx.AsyncClient) -> Dict[str, List[str]]:
    query = """
    SELECT axis_name, label
    FROM mcp_llm_axis_scores
    """
    response = await client.post("/query", json={"query": query})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to query database")

    data = response.json()
    axis_labels = {}
    for row in data:
        axis_name = row["axis_name"]
        label = row["label"]
        if axis_name not in axis_labels:
            axis_labels[axis_name] = []
        if label not in axis_labels[axis_name]:
            axis_labels[axis_name].append(label)
    return axis_labels

@router.get("/risk_axes/labels", response_model=RiskAxisLabelsResponse)
async def get_risk_axis_labels(client: httpx.AsyncClient = Depends(get_write_service_client)):
    axis_labels = await query_axis_labels(client)
    return {"risk_axes": axis_labels}

if __name__ == '__main__':
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Mock test client setup
    class MockAsyncClient:
        async def post(self, url, json):
            mock_data = [
                {"axis_name": "overall_risk", "label": "low"},
                {"axis_name": "overall_risk", "label": "medium"},
                {"axis_name": "overall_risk", "label": "high"},
                {"axis_name": "auth_strength", "label": "weak"},
                {"axis_name": "auth_strength", "label": "strong"},
                {"axis_name": "data_sensitivity", "label": "low"},
                {"axis_name": "data_sensitivity", "label": "high"},
            ]
            return httpx.Response(200, json=mock_data)

    app.dependency_overrides[get_write_service_client] = lambda: MockAsyncClient()

    client = TestClient(app)
    response = client.get("/risk_axes/labels")
    assert response.status_code == 200
    assert response.json() == {
        "risk_axes": {
            "overall_risk": ["low", "medium", "high"],
            "auth_strength": ["weak", "strong"],
            "data_sensitivity": ["low", "high"]
        }
    }
    print("PASS")