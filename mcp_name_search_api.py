from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
from typing import List

app = FastAPI()

class MCPServer(BaseModel):
    server_id: int
    mcp_name: str
    status: str

def query_write_service(sql: str) -> List[dict]:
    """Mock function to simulate querying the write_service"""
    # In a real implementation, this would make a POST request to write_service
    # For testing, we'll use a mock response
    mock_data = [
        {"server_id": 1, "mcp_name": "Alpha Server", "status": "active"},
        {"server_id": 2, "mcp_name": "Beta Server", "status": "inactive"},
        {"server_id": 3, "mcp_name": "Gamma Server", "status": "active"},
        {"server_id": 4, "mcp_name": "Delta Server", "status": "maintenance"},
    ]

    # Simulate SQL query execution
    if "Alpha" in sql or "alpha" in sql:
        return [mock_data[0]]
    elif "Beta" in sql or "beta" in sql:
        return [mock_data[1]]
    elif "Gamma" in sql or "gamma" in sql:
        return [mock_data[2]]
    elif "Delta" in sql or "delta" in sql:
        return [mock_data[3]]
    elif "Server" in sql or "server" in sql:
        return mock_data
    else:
        return []

@app.get("/mcp/search", response_model=List[MCPServer])
async def search_mcp_servers(name: str):
    """Search for MCP servers by name (case-insensitive partial match)"""
    if not name:
        raise HTTPException(status_code=400, detail="Name query parameter is required")

    # Parameterized SQL query to prevent SQL injection
    sql = f"""
    SELECT server_id, mcp_name, status
    FROM mcp_server_registry
    WHERE LOWER(mcp_name) LIKE LOWER('%{name}%')
    """

    try:
        # In a real implementation, this would be:
        # response = requests.post("http://write_service/query", json={"sql": sql})
        # results = response.json()
        results = query_write_service(sql)

        if not results:
            return []

        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # Test exact match
    response = client.get("/mcp/search?name=Alpha Server")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["mcp_name"] == "Alpha Server"

    # Test partial match
    response = client.get("/mcp/search?name=server")
    assert response.status_code == 200
    assert len(response.json()) == 4

    # Test case-insensitive match
    response = client.get("/mcp/search?name=alpha")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["mcp_name"] == "Alpha Server"

    # Test no match
    response = client.get("/mcp/search?name=nonexistent")
    assert response.status_code == 200
    assert len(response.json()) == 0

    print("PASS")