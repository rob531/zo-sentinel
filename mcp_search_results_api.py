from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
import requests
from typing import Optional, List

app = FastAPI()

class MCP(BaseModel):
    mcp_name: str
    server_id: str
    risk_tier: str
    overall_risk: str

class SearchResults(BaseModel):
    results: List[MCP]
    total: int

def query_write_service(search_term: str, risk_tier: Optional[str] = None, limit: int = 10, offset: int = 0) -> SearchResults:
    url = "http://write_service/query"
    params = {
        "query": f"SELECT mcp_name, server_id, risk_tier, overall_risk FROM mcp_server_registry WHERE mcp_name LIKE %s OR server_id LIKE %s",
        "params": (f"%{search_term}%", f"%{search_term}%"),
        "limit": limit,
        "offset": offset
    }

    if risk_tier:
        params["query"] += " AND risk_tier = %s"
        params["params"] += (risk_tier,)

    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()

    return SearchResults(
        results=[MCP(**item) for item in data["results"]],
        total=data["total"]
    )

@app.get("/mcp/search", response_model=SearchResults)
async def search_mcps(
    search_term: str = Query(..., min_length=1),
    risk_tier: Optional[str] = Query(None),
    limit: int = Query(10, gt=0, le=100),
    offset: int = Query(0, ge=0)
):
    try:
        results = query_write_service(search_term, risk_tier, limit, offset)
        return results
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # Test 1: Basic search
    response = client.get("/mcp/search?search_term=test")
    assert response.status_code == 200
    assert len(response.json()["results"]) > 0

    # Test 2: Search with risk tier filter
    response = client.get("/mcp/search?search_term=test&risk_tier=high")
    assert response.status_code == 200
    assert all(mcp["risk_tier"] == "high" for mcp in response.json()["results"])

    # Test 3: Pagination
    response = client.get("/mcp/search?search_term=test&limit=1")
    assert response.status_code == 200
    assert len(response.json()["results"]) == 1

    print("PASS")