from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
import requests
from typing import Optional

app = FastAPI()

class LLMScoreResponse(BaseModel):
    axis_scores: dict
    overall_score: float
    computed_at: str
    server_id: str

class LLMScoreRequest(BaseModel):
    server_id: str

WRITE_SERVICE_URL = "http://write_service:8000"

@app.get("/mcp/{server_id}/llm_axis_scores", response_model=LLMScoreResponse)
async def get_llm_axis_scores(server_id: str):
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={
                "query": f"SELECT * FROM mcp_llm_axis_scores WHERE server_id = '{server_id}' ORDER BY computed_at DESC LIMIT 1"
            }
        )
        response.raise_for_status()
        data = response.json()

        if not data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No scores found for the given server_id"
            )

        result = data[0]
        return {
            "axis_scores": {
                "axis1": result["axis1"],
                "axis2": result["axis2"],
                "axis3": result["axis3"],
                "axis4": result["axis4"],
                "axis5": result["axis5"],
                "axis6": result["axis6"]
            },
            "overall_score": result["overall_score"],
            "computed_at": result["computed_at"],
            "server_id": result["server_id"]
        }

    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error querying write service: {str(e)}"
        )

if __name__ == "__main__":
    import uvicorn
    from fastapi.testclient import TestClient

    # Mock data for testing
    test_data = {
        "axis1": 0.8,
        "axis2": 0.6,
        "axis3": 0.7,
        "axis4": 0.5,
        "axis5": 0.9,
        "axis6": 0.4,
        "overall_score": 0.65,
        "computed_at": "2023-01-01T00:00:00Z",
        "server_id": "test_server_1"
    }

    # Mock write service response
    def mock_post(url, json):
        if "test_server_1" in json["query"]:
            return type('Response', (), {
                'status_code': 200,
                'json': lambda: [test_data]
            })()
        return type('Response', (), {
            'status_code': 200,
            'json': lambda: []
        })()

    requests.post = mock_post

    client = TestClient(app)

    # Test existing server_id
    response = client.get("/mcp/test_server_1/llm_axis_scores")
    assert response.status_code == 200
    assert response.json()["axis_scores"]["axis1"] == 0.8
    assert response.json()["overall_score"] == 0.65

    # Test non-existent server_id
    response = client.get("/mcp/non_existent_server/llm_axis_scores")
    assert response.status_code == 404