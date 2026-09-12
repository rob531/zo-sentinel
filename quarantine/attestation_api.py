from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
import requests
from typing import List, Optional
from datetime import datetime

app = FastAPI()

class Attestation(BaseModel):
    attestation_id: str
    mcp_id: str
    attestation_type: str
    status: str
    created_at: datetime
    expires_at: datetime

class AttestationDetail(Attestation):
    # Additional fields for detailed view can be added here if needed
    pass

def query_write_service(sql: str, params: tuple = ()) -> List[dict]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": sql, "params": params}
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/attestations", response_model=List[Attestation])
async def get_attestations():
    sql = """
    SELECT attestation_id, mcp_id, attestation_type, status, created_at, expires_at
    FROM attestations
    """
    results = query_write_service(sql)
    return results

@app.get("/attestations/{attestation_id}", response_model=AttestationDetail)
async def get_attestation(attestation_id: str = Path(...)):
    sql = """
    SELECT attestation_id, mcp_id, attestation_type, status, created_at, expires_at
    FROM attestations
    WHERE attestation_id = ?
    """
    results = query_write_service(sql, (attestation_id,))
    if not results:
        raise HTTPException(status_code=404, detail="Attestation not found")
    return results[0]

if __name__ == "__main__":
    from fastapi.testclient import TestClient

    # Mock the write_service responses
    def mock_query_write_service(sql: str, params: tuple = ()) -> List[dict]:
        if "SELECT attestation_id, mcp_id, attestation_type, status, created_at, expires_at FROM attestations" in sql:
            return [
                {
                    "attestation_id": "att1",
                    "mcp_id": "mcp1",
                    "attestation_type": "type1",
                    "status": "active",
                    "created_at": "2023-01-01T00:00:00",
                    "expires_at": "2023-12-31T00:00:00"
                },
                {
                    "attestation_id": "att2",
                    "mcp_id": "mcp2",
                    "attestation_type": "type2",
                    "status": "inactive",
                    "created_at": "2023-02-01T00:00:00",
                    "expires_at": "2023-11-30T00:00:00"
                }
            ]
        elif "WHERE attestation_id = ?" in sql and params == ("att1",):
            return [
                {
                    "attestation_id": "att1",
                    "mcp_id": "mcp1",
                    "attestation_type": "type1",
                    "status": "active",
                    "created_at": "2023-01-01T00:00:00",
                    "expires_at": "2023-12-31T00:00:00"
                }
            ]
        else:
            return []

    # Replace the real query function with the mock
    original_query = query_write_service
    query_write_service = mock_query_write_service

    client = TestClient(app)

    # Test GET /attestations
    response = client.get("/attestations")
    assert response.status_code == 200
    assert len(response.json()) > 0

    # Test GET /attestations/{attestation_id}
    response = client.get("/attestations/att1")
    assert response.status_code == 200
    assert response.json()["attestation_id"] == "att1"

    # Restore the original query function
    query_write_service = original_query

    print("PASS")