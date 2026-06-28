import datetime
from typing import List, Optional

from fastapi import APIRouter, FastAPI, Query
from pydantic import BaseModel

# For testing in __main__
from fastapi.testclient import TestClient


# Pydantic model for an MCP Attestation record
class Attestation(BaseModel):
    """
    Represents a single MCP attestation record.
    """
    id: str
    server_id: str
    attestation_date: datetime.datetime
    attestation_data: dict


# In-memory data store to simulate a database table for read-only access.
# In a real application, this would be replaced with a database connection and ORM.
_MOCK_ATTESTATIONS_DB: List[Attestation] = [
    Attestation(
        id="attest_001",
        server_id="server_alpha",
        attestation_date=datetime.datetime(2023, 1, 15, 10, 0, 0),
        attestation_data={"cpu_temp": 45, "disk_usage": 60, "status": "healthy"},
    ),
    Attestation(
        id="attest_002",
        server_id="server_beta",
        attestation_date=datetime.datetime(2023, 1, 15, 10, 30, 0),
        attestation_data={"memory_usage": 70, "network_latency": 10, "status": "healthy"},
    ),
    Attestation(
        id="attest_003",
        server_id="server_alpha",
        attestation_date=datetime.datetime(2023, 1, 16, 11, 0, 0),
        attestation_data={"cpu_temp": 48, "disk_usage": 62, "status": "warning"},
    ),
    Attestation(
        id="attest_004",
        server_id="server_gamma",
        attestation_date=datetime.datetime(2023, 1, 16, 11, 15, 0),
        attestation_data={"uptime_hours": 72, "process_count": 120, "status": "healthy"},
    ),
    Attestation(
        id="attest_005",
        server_id="server_alpha",
        attestation_date=datetime.datetime(2023, 1, 17, 9, 0, 0),
        attestation_data={"cpu_temp": 46, "disk_usage": 61, "status": "healthy"},
    ),
    Attestation(
        id="attest_006",
        server_id="server_beta",
        attestation_date=datetime.datetime(2023, 1, 17, 9, 30, 0),
        attestation_data={"memory_usage": 72, "network_latency": 12, "status": "healthy"},
    ),
    Attestation(
        id="attest_007",
        server_id="server_alpha",
        attestation_date=datetime.datetime(2023, 1, 15, 12, 0, 0), # Another one for server_alpha on 2023-01-15
        attestation_data={"cpu_temp": 44, "disk_usage": 58, "status": "healthy"},
    ),
]

# FastAPI router for MCP attestations
router = APIRouter()


@router.get(
    "/mcp/attestations",
    response_model=List[Attestation],
    summary="List all MCP attestations with optional filtering",
    description="Retrieves a list of all MCP attestations. Can be filtered by server ID and attestation date.",
)
async def list_mcp_attestations(
    server_id: Optional[str] = Query(
        None,
        description="Filter attestations by a specific server ID.",
        example="server_alpha",
    ),
    attestation_date: Optional[datetime.date] = Query(
        None,
        description="Filter attestations by a specific date (YYYY-MM-DD format).",
        example="2023-01-15",
    ),
) -> List[Attestation]:
    """
    Retrieves a list of MCP attestation records.

    This endpoint allows clients to fetch all attestation records or filter them
    based on the `server_id` and/or `attestation_date`.

    Args:
        server_id (Optional[str]): The unique identifier of the server whose
                                    attestations are to be retrieved.
        attestation_date (Optional[datetime.date]): The specific calendar date
                                                    (e.g., '2023-01-15') for which
                                                    attestations are to be retrieved.

    Returns:
        List[Attestation]: A JSON array of attestation records matching the
                           specified criteria. If no filters are provided, all
                           available attestations are returned.
    """
    filtered_attestations = _MOCK_ATTESTATIONS_DB

    if server_id:
        filtered_attestations = [
            att for att in filtered_attestations if att.server_id == server_id
        ]

    if attestation_date:
        # Compare only the date part of the datetime object stored in the data
        filtered_attestations = [
            att
            for att in filtered_attestations
            if att.attestation_date.date() == attestation_date
        ]

    return filtered_attestations


# __main__ block for running tests with FastAPI TestClient
if __name__ == "__main__":
    # Create a FastAPI application instance
    app = FastAPI(
        title="MCP Attestations API",
        description="API for managing MCP attestation records.",
        version="1.0.0",
    )
    # Include the attestation router in the main application
    app.include_router(router)

    # Create a TestClient for making requests to the application
    client = TestClient(app)

    print("--- Running FastAPI TestClient assertions for /mcp/attestations endpoint ---")

    # Test Case 1: No filters - should return all attestations
    print("\nTest 1: Query without filters (expect all attestations)")
    response = client.get("/mcp/attestations")
    assert response.status_code == 200, f"FAIL: Expected status 200, got {response.status_code}"
    assert len(response.json()) == len(_MOCK_ATTESTATIONS_DB), f"FAIL: Expected {len(_MOCK_ATTESTATIONS_DB)} attestations, got {len(response.json())}"
    print(f"PASS: Found {len(response.json())} attestations as expected.")

    # Test Case 2: Filter by server_id (server_alpha)
    print("\nTest 2: Query with server_id=server_alpha (expect 4 attestations)")
    response = client.get("/mcp/attestations?server_id=server_alpha")
    assert response.status_code == 200, f"FAIL: Expected status 200, got {response.status_code}"
    assert len(response.json()) == 4, f"FAIL: Expected 4 attestations for server_alpha, got {len(response.json())}"
    assert all(
        att["server_id"] == "server_alpha" for att in response.json()
    ), "FAIL: Not all returned attestations are for server_alpha"
    print(f"PASS: Found {len(response.json())} attestations for server_alpha.")

    # Test Case 3: Filter by server_id (non-existent)
    print("\nTest 3: Query with non-existent server_id (expect 0 attestations)")
    response = client.get("/mcp/attestations?server_id=non_existent_server")
    assert response.status_code == 200, f"FAIL: Expected status 200, got {response.status_code}"
    assert len(response.json()) == 0, f"FAIL: Expected 0 attestations, got {len(response.json())}"
    print(f"PASS: Found {len(response.json())} attestations for non-existent server_id.")

    # Test Case 4: Filter by attestation_date (2023-01-15)
    print("\nTest 4: Query with attestation_date=2023-01-15 (expect 3 attestations)")
    response = client.get("/mcp/attestations?attestation_date=2023-01-15")
    assert response.status_code == 200, f"FAIL: Expected status 200, got {response.status_code}"
    assert len(response.json()) == 3, f"FAIL: Expected 3 attestations for 2023-01-15, got {len(response.json())}"
    assert all(
        datetime.datetime.fromisoformat(att["attestation_date"]).date()
        == datetime.date(2023, 1, 15)
        for att in response.json()
    ), "FAIL: Not all returned attestations are for 2023-01-15"
    print(f"PASS: Found {len(response.json())} attestations for 2023-01-15.")

    # Test Case 5: Filter by attestation_date (non-existent)
    print("\nTest 5: Query with non-existent attestation_date (expect 0 attestations)")
    response = client.get("/mcp/attestations?attestation_date=2023-02-01")
    assert response.status_code == 200, f"FAIL: Expected status 200, got {response.status_code}"
    assert len(response.json()) == 0, f"FAIL: Expected 0 attestations, got {len(response.json())}"
    print(f"PASS: Found {len(response.json())} attestations for non-existent date.")

    # Test Case 6: Filter by both server_id and attestation_date (server_alpha, 2023-01-16)
    print("\nTest 6: Query with server_id=server_alpha & attestation_date=2023-01-16 (expect 1 attestation)")
    response = client.get("/mcp/attestations?server_id=server_alpha&attestation_date=2023-01-16")
    assert response.status_code == 200, f"FAIL: Expected status 200, got {response.status_code}"
    assert len(response.json()) == 1, f"FAIL: Expected 1 attestation, got {len(response.json())}"
    assert response.json()[0]["id"] == "attest_003", "FAIL: Incorrect attestation returned"
    print(f"PASS: Found {len(response.json())} attestation for server_alpha on 2023-01-16.")

    # Test Case 7: Filter by both server_id and attestation_date (server_alpha, 2023-01-15)
    print("\nTest 7: Query with server_id=server_alpha & attestation_date=2023-01-15 (expect 2 attestations)")
    response = client.get("/mcp/attestations?server_id=server_alpha&attestation_date=2023-01-15")
    assert response.status_code == 200, f"FAIL: Expected status 200, got {response.status_code}"
    assert len(response.json()) == 2, f"FAIL: Expected 2 attestations, got {len(response.json())}"
    assert all(att["id"] in ["attest_001", "attest_007"] for att in response.json()), "FAIL: Incorrect attestations returned"
    print(f"PASS: Found {len(response.json())} attestations for server_alpha on 2023-01-15.")

    # Test Case 8: Filter by both server_id and attestation_date (no match)
    print("\nTest 8: Query with server_id=server_gamma & attestation_date=2023-01-15 (expect 0 attestations)")
    response = client.get("/mcp/attestations?server_id=server_gamma&attestation_date=2023-01-15")
    assert response.status_code == 200, f"FAIL: Expected status 200, got {response.status_code}"
    assert len(response.json()) == 0, f"FAIL: Expected 0 attestations, got {len(response.json())}"
    print(f"PASS: Found {len(response.json())} attestations for no-match criteria.")

    # Test Case 9: Invalid date format (FastAPI should return 422 Unprocessable Entity)
    print("\nTest 9: Query with invalid attestation_date format (expect 422 status)")
    response = client.get("/mcp/attestations?attestation_date=invalid-date-format")
    assert response.status_code == 422, f"FAIL: Expected status 422, got {response.status_code}"
    print(f"PASS: Received expected status code {response.status_code} for invalid date format.")

    print("\n--- All TestClient assertions completed successfully! ---")

    # Optional: To run the server itself (e.g., with `uvicorn mcp_attestations_api:app --reload`)
    # if you remove the `if __name__ == "__main__":` block and just run `uvicorn`.
    # For this task, the `__main__` block is specifically for TestClient.