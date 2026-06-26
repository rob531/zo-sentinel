# mcp_fingerprints_api.py

from fastapi import APIRouter, HTTPException, FastAPI
from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional
import json
from fastapi.testclient import TestClient

# --- Pydantic Models ---

class Fingerprint(BaseModel):
    """
    Represents a fingerprint entry in the mcp_fingerprints table.
    """
    fingerprint_id: str
    mcp_id: str
    fingerprint_hash: str
    created_at: datetime
    # Add any other relevant fields here if specified in the schema
    # For this task, we stick to the explicitly mentioned fields.

class FingerprintResponse(Fingerprint):
    """
    Response model for a single fingerprint retrieval.
    Inherits all fields from Fingerprint.
    """
    pass

class FingerprintListResponse(BaseModel):
    """
    Response model for retrieving a list of fingerprints.
    """
    fingerprints: List[Fingerprint]

# --- Simulated In-Memory Database (for testing) ---
# In a real application, this would be replaced with actual database connection
# and query execution using a library like psycopg2 or asyncpg.

_IN_MEMORY_DB: List[dict] = []

def _seed_db():
    """
    Seeds the in-memory database with sample data for testing.
    """
    global _IN_MEMORY_DB
    _IN_MEMORY_DB = [
        {
            "fingerprint_id": "fp_123",
            "mcp_id": "mcp_A",
            "fingerprint_hash": "hash_abc_123",
            "created_at": datetime(2023, 1, 1, 10, 0, 0),
        },
        {
            "fingerprint_id": "fp_456",
            "mcp_id": "mcp_A",
            "fingerprint_hash": "hash_def_456",
            "created_at": datetime(2023, 1, 1, 11, 0, 0),
        },
        {
            "fingerprint_id": "fp_789",
            "mcp_id": "mcp_B",
            "fingerprint_hash": "hash_ghi_789",
            "created_at": datetime(2023, 1, 2, 12, 0, 0),
        },
        {
            "fingerprint_id": "fp_012",
            "mcp_id": "mcp_C",
            "fingerprint_hash": "hash_jkl_012",
            "created_at": datetime(2023, 1, 3, 13, 0, 0),
        },
    ]

# --- Database Query Simulation Functions (Postgres-portable SQL concept) ---
# These functions simulate executing SQL queries against a PostgreSQL database.
# For this task, they operate on the in-memory list.

def get_fingerprint_by_id_sql(fingerprint_id: str) -> Optional[dict]:
    """
    Simulates a SQL query to retrieve a single fingerprint by its ID.
    Equivalent to: SELECT fingerprint_id, mcp_id, fingerprint_hash, created_at
                   FROM mcp_fingerprints WHERE fingerprint_id = %s;
    """
    for fp in _IN_MEMORY_DB:
        if fp["fingerprint_id"] == fingerprint_id:
            return fp
    return None

def get_fingerprints_by_mcp_id_sql(mcp_id: str) -> List[dict]:
    """
    Simulates a SQL query to retrieve all fingerprints for a given MCP ID.
    Equivalent to: SELECT fingerprint_id, mcp_id, fingerprint_hash, created_at
                   FROM mcp_fingerprints WHERE mcp_id = %s;
    """
    return [fp for fp in _IN_MEMORY_DB if fp["mcp_id"] == mcp_id]

# --- FastAPI Router Definition ---

router = APIRouter()

@router.get(
    "/fingerprints/{fingerprint_id}",
    response_model=FingerprintResponse,
    summary="Retrieve a specific fingerprint by its ID",
    description="Fetches details for a single fingerprint using its unique identifier."
)
async def get_fingerprint(fingerprint_id: str):
    """
    Retrieves details for a specific `fingerprint_id` from the `mcp_fingerprints` table.
    """
    fingerprint_data = get_fingerprint_by_id_sql(fingerprint_id)
    if not fingerprint_data:
        raise HTTPException(status_code=404, detail="Fingerprint not found")
    return Fingerprint(**fingerprint_data)

@router.get(
    "/fingerprints/by_mcp/{mcp_id}",
    response_model=FingerprintListResponse,
    summary="Retrieve all fingerprints associated with an MCP ID",
    description="Fetches a list of all fingerprints linked to a given MCP identifier."
)
async def get_fingerprints_by_mcp(mcp_id: str):
    """
    Retrieves all fingerprints associated with a given `mcp_id`.
    Returns an empty list if no fingerprints are found for the MCP ID.
    """
    fingerprints_data = get_fingerprints_by_mcp_id_sql(mcp_id)
    # The response_model expects a dictionary with a 'fingerprints' key
    return {"fingerprints": [Fingerprint(**fp) for fp in fingerprints_data]}

# --- Acceptance Testing with TestClient ---

if __name__ == "__main__":
    _seed_db()  # Seed the in-memory database for testing

    app = FastAPI(
        title="MCP Fingerprints API",
        description="API for managing and retrieving MCP fingerprints.",
        version="1.0.0"
    )
    app.include_router(router, prefix="/api/v1") # Include router with a prefix

    client = TestClient(app)

    print("Running acceptance tests for mcp_fingerprints_api.py...\n")

    # Test Case 1: GET /fingerprints/{fingerprint_id} - Known ID
    print("Test 1: GET /api/v1/fingerprints/fp_123 (Known ID)")
    response = client.get("/api/v1/fingerprints/fp_123")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["fingerprint_id"] == "fp_123"
    assert data["mcp_id"] == "mcp_A"
    assert data["fingerprint_hash"] == "hash_abc_123"
    assert "created_at" in data
    print(f"  Response: {json.dumps(data, indent=2)}")
    print("  Test 1 Passed.\n")

    # Test Case 2: GET /fingerprints/{fingerprint_id} - Unknown ID
    print("Test 2: GET /api/v1/fingerprints/unknown_id (Unknown ID)")
    response = client.get("/api/v1/fingerprints/unknown_id")
    assert response.status_code == 404, f"Expected 404, got {response.status_code}"
    assert response.json() == {"detail": "Fingerprint not found"}
    print(f"  Response: {json.dumps(response.json(), indent=2)}")
    print("  Test 2 Passed.\n")

    # Test Case 3: GET /fingerprints/by_mcp/{mcp_id} - Known MCP with multiple fingerprints
    print("Test 3: GET /api/v1/fingerprints/by_mcp/mcp_A (Multiple Fingerprints)")
    response = client.get("/api/v1/fingerprints/by_mcp/mcp_A")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert "fingerprints" in data
    assert len(data["fingerprints"]) == 2
    assert data["fingerprints"][0]["mcp_id"] == "mcp_A"
    assert data["fingerprints"][1]["mcp_id"] == "mcp_A"
    print(f"  Response: {json.dumps(data, indent=2)}")
    print("  Test 3 Passed.\n")

    # Test Case 4: GET /fingerprints/by_mcp/{mcp_id} - Known MCP with single fingerprint
    print("Test 4: GET /api/v1/fingerprints/by_mcp/mcp_B (Single Fingerprint)")
    response = client.get("/api/v1/fingerprints/by_mcp/mcp_B")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert "fingerprints" in data
    assert len(data["fingerprints"]) == 1
    assert data["fingerprints"][0]["mcp_id"] == "mcp_B"
    print(f"  Response: {json.dumps(data, indent=2)}")
    print("  Test 4 Passed.\n")

    # Test Case 5: GET /fingerprints/by_mcp/{mcp_id} - Unknown MCP (should return empty list)
    print("Test 5: GET /api/v1/fingerprints/by_mcp/mcp_Z (Unknown MCP)")
    response = client.get("/api/v1/fingerprints/by_mcp/mcp_Z")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert "fingerprints" in data
    assert len(data["fingerprints"]) == 0
    print(f"  Response: {json.dumps(data, indent=2)}")
    print("  Test 5 Passed.\n")

    print("All acceptance tests passed. PASS")