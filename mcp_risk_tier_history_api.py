from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient
from datetime import datetime
import requests

router = APIRouter()

@router.get("/mcp/{server_id}/risk_history")
async def get_risk_history(server_id: str):
    query = """
    SELECT computed_at, risk_tier, overall_score
    FROM mcp_risk_register
    WHERE server_id = %s
    ORDER BY computed_at
    """
    params = (server_id,)

    try:
        response = requests.post("http://write_service/query", json={"query": query, "params": params})
        response.raise_for_status()
        data = response.json()

        risk_history = []
        for row in data:
            risk_history.append({
                'timestamp': row['computed_at'],
                'risk_tier': row['risk_tier'],
                'overall_score': row['overall_score']
            })

        return risk_history
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Seed some historical data for testing
    seed_data = [
        {"server_id": "test_server", "computed_at": "2023-01-01T00:00:00", "risk_tier": "TIER_1", "overall_score": 0.8},
        {"server_id": "test_server", "computed_at": "2023-01-02T00:00:00", "risk_tier": "TIER_2", "overall_score": 0.6},
        {"server_id": "test_server", "computed_at": "2023-01-03T00:00:00", "risk_tier": "TIER_1", "overall_score": 0.7},
    ]

    # Mock the write_service response
    def mock_query(request):
        query = request.json()["query"]
        params = request.json()["params"]

        if "mcp_risk_register" in query and params[0] == "test_server":
            return {"data": seed_data}

        return {"data": []}

    # Test the API
    response = client.get("/mcp/test_server/risk_history")
    assert response.status_code == 200

    data = response.json()
    assert len(data) == 3

    # Check the order of timestamps
    timestamps = [datetime.fromisoformat(item['timestamp']) for item in data]
    assert timestamps == sorted(timestamps)

    # Check the risk tiers and scores
    assert data[0]['risk_tier'] == "TIER_1"
    assert data[0]['overall_score'] == 0.8
    assert data[1]['risk_tier'] == "TIER_2"
    assert data[1]['overall_score'] == 0.6
    assert data[2]['risk_tier'] == "TIER_1"
    assert data[2]['overall_score'] == 0.7

    print("PASS")