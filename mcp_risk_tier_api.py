from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient
import requests

router = APIRouter()

@router.get("/mcp/{mcp_id}/risk_tier")
async def get_risk_tier(mcp_id: str):
    query = "SELECT risk_tier FROM mcp_risk_register WHERE mcp_id = %s ORDER BY created_at DESC LIMIT 1"
    params = (mcp_id,)
    response = requests.post("http://127.0.0.1:8772/query", json={"query": query, "params": params})

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error querying the database")

    data = response.json()
    if not data:
        raise HTTPException(status_code=404, detail="MCP ID not found")

    return {"mcp_id": mcp_id, "risk_tier": data[0]["risk_tier"]}

if __name__ == "__main__":
    from fastapi import FastAPI
    import pytest
    from unittest.mock import patch

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    def test_get_risk_tier():
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = [{"risk_tier": "TRUSTED_GENERAL"}]

            response = client.get("/mcp/test_mcp_id/risk_tier")
            assert response.status_code == 200
            assert response.json() == {"mcp_id": "test_mcp_id", "risk_tier": "TRUSTED_GENERAL"}

            mock_post.return_value.json.return_value = []
            response = client.get("/mcp/nonexistent_mcp_id/risk_tier")
            assert response.status_code == 404

    test_get_risk_tier()
    print("PASS")