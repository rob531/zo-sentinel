from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from fastapi.testclient import TestClient
import json

# Assume write_service is a module that handles database interactions
# For this example, we'll mock it.
class MockWriteService:
    def __init__(self):
        self.attestations = {
            1: {"id": 1, "requester": "user1", "status": "pending", "details": "Needs review 1"},
            2: {"id": 2, "requester": "user2", "status": "pending", "details": "Needs review 2"},
            3: {"id": 3, "requester": "user3", "status": "approved", "details": "Already approved"},
        }

    def fetch_all(self, query: str, params: dict = None):
        if "SELECT * FROM mcp_attestations WHERE status = :status" in query:
            status_to_filter = params.get("status")
            return [att for att in self.attestations.values() if att["status"] == status_to_filter]
        elif "SELECT * FROM mcp_attestations WHERE id = :attestation_id" in query:
            att_id = params.get("attestation_id")
            return [self.attestations.get(att_id)] if att_id in self.attestations else []
        return []

    def execute(self, query: str, params: dict = None):
        if "UPDATE mcp_attestations SET status = :new_status WHERE id = :attestation_id" in query:
            att_id = params.get("attestation_id")
            new_status = params.get("new_status")
            if att_id in self.attestations:
                self.attestations[att_id]["status"] = new_status
                return 1  # Simulate one row updated
            return 0  # Simulate zero rows updated
        return 0

write_service = MockWriteService()

app = FastAPI()

class Attestation(BaseModel):
    id: int
    requester: str
    status: str
    details: Optional[str] = None

@app.get("/attestations/pending", response_model=List[Attestation])
async def get_pending_attestations():
    """
    Retrieves a list of all pending MCP attestations.
    """
    query = "SELECT * FROM mcp_attestations WHERE status = :status"
    params = {"status": "pending"}
    attestations_data = write_service.fetch_all(query, params)
    return [Attestation(**att) for att in attestations_data]

@app.get("/attestations/{attestation_id}", response_model=Attestation)
async def get_attestation_details(attestation_id: int):
    """
    Retrieves details of a specific MCP attestation by its ID.
    """
    query = "SELECT * FROM mcp_attestations WHERE id = :attestation_id"
    params = {"attestation_id": attestation_id}
    attestation_data = write_service.fetch_all(query, params)
    if not attestation_data:
        raise HTTPException(status_code=404, detail="Attestation not found")
    return Attestation(**attestation_data[0])

@app.post("/attestations/{attestation_id}/review")
async def review_attestation(attestation_id: int):
    """
    Marks an attestation as reviewed.
    """
    query = "UPDATE mcp_attestations SET status = :new_status WHERE id = :attestation_id"
    params = {"attestation_id": attestation_id, "new_status": "reviewed"}
    rows_updated = write_service.execute(query, params)
    if rows_updated == 0:
        raise HTTPException(status_code=404, detail="Attestation not found or already reviewed")
    return {"message": f"Attestation {attestation_id} marked as reviewed"}

@app.post("/attestations/{attestation_id}/approve")
async def approve_attestation(attestation_id: int):
    """
    Marks an attestation as approved.
    """
    query = "UPDATE mcp_attestations SET status = :new_status WHERE id = :attestation_id"
    params = {"attestation_id": attestation_id, "new_status": "approved"}
    rows_updated = write_service.execute(query, params)
    if rows_updated == 0:
        raise HTTPException(status_code=404, detail="Attestation not found or already approved")
    return {"message": f"Attestation {attestation_id} marked as approved"}

if __name__ == "__main__":
    client = TestClient(app)

    # Test case 1: Get pending attestations
    response = client.get("/attestations/pending")
    assert response.status_code == 200
    pending_attestations = response.json()
    assert len(pending_attestations) == 2
    assert any(att["id"] == 1 and att["status"] == "pending" for att in pending_attestations)
    assert any(att["id"] == 2 and att["status"] == "pending" for att in pending_attestations)
    print("Test Case 1: GET /attestations/pending - PASSED")

    # Test case 2: Get details of a specific attestation
    response = client.get("/attestations/1")
    assert response.status_code == 200
    attestation_details = response.json()
    assert attestation_details["id"] == 1
    assert attestation_details["status"] == "pending"
    print("Test Case 2: GET /attestations/{attestation_id} - PASSED")

    # Test case 3: Review an attestation
    response = client.post("/attestations/1/review")
    assert response.status_code == 200
    assert response.json() == {"message": "Attestation 1 marked as reviewed"}

    # Verify the status update
    response = client.get("/attestations/1")
    assert response.status_code == 200
    assert response.json()["status"] == "reviewed"
    print("Test Case 3: POST /attestations/{attestation_id}/review - PASSED")

    # Test case 4: Approve an attestation
    response = client.post("/attestations/2/approve")
    assert response.status_code == 200
    assert response.json() == {"message": "Attestation 2 marked as approved"}

    # Verify the status update
    response = client.get("/attestations/2")
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    print("Test Case 4: POST /attestations/{attestation_id}/approve - PASSED")

    # Test case 5: Attempt to get details of a non-existent attestation
    response = client.get("/attestations/999")
    assert response.status_code == 404
    print("Test Case 5: GET /attestations/{non_existent_id} - PASSED")

    # Test case 6: Attempt to review an already approved attestation (should fail if logic enforced)
    # In this mock, it will just update to 'reviewed' if it was 'approved'
    response = client.post("/attestations/3/review")
    assert response.status_code == 200
    assert response.json() == {"message": "Attestation 3 marked as reviewed"}
    response = client.get("/attestations/3")
    assert response.status_code == 200
    assert response.json()["status"] == "reviewed"
    print("Test Case 6: POST /attestations/{already_approved_id}/review - PASSED")


    print("\nAll tests passed!")