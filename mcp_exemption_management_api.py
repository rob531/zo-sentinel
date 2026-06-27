import datetime
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

# --- Pydantic Models ---

class MCPExemptionBase(BaseModel):
    """Base model for MCP exemption details."""
    mcp_id: str = Field(..., description="The ID of the MCP (Managed Control Point) being exempted.")
    reason: str = Field(..., description="Reason for the exemption.")
    expiry_date: datetime.date = Field(..., description="Date when the exemption expires.")

class MCPExemptionCreate(MCPExemptionBase):
    """Model for creating a new MCP exemption."""
    created_by: str = Field(..., description="User or system that created the exemption.")

class MCPExemptionUpdate(BaseModel):
    """Model for updating an existing MCP exemption."""
    mcp_id: Optional[str] = Field(None, description="The ID of the MCP (Managed Control Point) being exempted.")
    reason: Optional[str] = Field(None, description="Reason for the exemption.")
    expiry_date: Optional[datetime.date] = Field(None, description="Date when the exemption expires.")
    # created_by is not typically updated, created_at is system-managed

class MCPExemptionInDB(MCPExemptionBase):
    """Model representing an MCP exemption as stored in the database."""
    id: int = Field(..., description="Unique identifier for the exemption.")
    created_at: datetime.datetime = Field(..., description="Timestamp when the exemption was created.")
    created_by: str = Field(..., description="User or system that created the exemption.")

# --- In-memory Database Simulation ---
# This simulates interaction with an 'mcp_exemptions' table.
# In a real application, this would be replaced with SQLAlchemy, Pydantic-SQLAlchemy, etc.

db: Dict[int, MCPExemptionInDB] = {}
next_id: int = 1

# --- FastAPI Application ---

app = FastAPI(
    title="MCP Exemption Management API",
    description="API for managing exemptions for Managed Control Points (MCPs).",
    version="1.0.0",
)

@app.post(
    "/mcp/exemptions",
    response_model=MCPExemptionInDB,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new MCP exemption",
    response_description="The newly created MCP exemption details."
)
async def create_mcp_exemption(exemption: MCPExemptionCreate):
    """
    Creates a new exemption for a Managed Control Point (MCP).

    - **mcp_id**: The identifier of the MCP.
    - **reason**: The justification for granting the exemption.
    - **expiry_date**: The date on which the exemption will no longer be valid.
    - **created_by**: The user or system responsible for creating this exemption.
    """
    global next_id
    new_exemption_id = next_id
    next_id += 1

    now = datetime.datetime.now(datetime.timezone.utc)
    db_exemption = MCPExemptionInDB(
        id=new_exemption_id,
        created_at=now,
        **exemption.dict()
    )
    db[new_exemption_id] = db_exemption
    return db_exemption

@app.get(
    "/mcp/exemptions/{exemption_id}",
    response_model=MCPExemptionInDB,
    summary="Retrieve an MCP exemption by ID",
    response_description="The requested MCP exemption details."
)
async def get_mcp_exemption(exemption_id: int):
    """
    Retrieves the details of a specific MCP exemption by its unique ID.

    - **exemption_id**: The unique identifier of the exemption to retrieve.
    """
    exemption = db.get(exemption_id)
    if not exemption:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP exemption with ID {exemption_id} not found."
        )
    return exemption

@app.put(
    "/mcp/exemptions/{exemption_id}",
    response_model=MCPExemptionInDB,
    summary="Update an existing MCP exemption",
    response_description="The updated MCP exemption details."
)
async def update_mcp_exemption(exemption_id: int, exemption_update: MCPExemptionUpdate):
    """
    Updates the details of an existing MCP exemption.

    - **exemption_id**: The unique identifier of the exemption to update.
    - **mcp_id**: (Optional) New MCP ID.
    - **reason**: (Optional) New reason for the exemption.
    - **expiry_date**: (Optional) New expiry date for the exemption.
    """
    existing_exemption = db.get(exemption_id)
    if not existing_exemption:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP exemption with ID {exemption_id} not found."
        )

    update_data = exemption_update.dict(exclude_unset=True)
    updated_fields = existing_exemption.copy(update=update_data)
    db[exemption_id] = updated_fields
    return updated_fields

@app.delete(
    "/mcp/exemptions/{exemption_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an MCP exemption",
    response_description="Confirmation of successful deletion."
)
async def delete_mcp_exemption(exemption_id: int):
    """
    Deletes an MCP exemption by its unique ID.

    - **exemption_id**: The unique identifier of the exemption to delete.
    """
    if exemption_id not in db:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP exemption with ID {exemption_id} not found."
        )
    del db[exemption_id]
    return {"message": f"MCP exemption with ID {exemption_id} deleted successfully."}


# --- Acceptance Tests (using TestClient) ---
if __name__ == "__main__":
    client = TestClient(app)

    print("--- Starting Acceptance Tests ---")

    # 1. Test POST: Create an exemption
    print("\nTesting POST /mcp/exemptions (Create)")
    create_payload = {
        "mcp_id": "MCP-001",
        "reason": "Temporary maintenance window",
        "expiry_date": "2023-12-31",
        "created_by": "admin_user"
    }
    response = client.post("/mcp/exemptions", json=create_payload)
    assert response.status_code == status.HTTP_201_CREATED, f"Expected 201, got {response.status_code}"
    created_exemption = response.json()
    assert created_exemption["mcp_id"] == "MCP-001"
    assert created_exemption["reason"] == "Temporary maintenance window"
    assert created_exemption["expiry_date"] == "2023-12-31"
    assert "id" in created_exemption
    assert "created_at" in created_exemption
    exemption_id = created_exemption["id"]
    print(f"Created exemption: {created_exemption}")
    print(f"Exemption ID: {exemption_id}")

    # 2. Test GET: Retrieve the created exemption
    print(f"\nTesting GET /mcp/exemptions/{exemption_id} (Retrieve existing)")
    response = client.get(f"/mcp/exemptions/{exemption_id}")
    assert response.status_code == status.HTTP_200_OK, f"Expected 200, got {response.status_code}"
    retrieved_exemption = response.json()
    assert retrieved_exemption["id"] == exemption_id
    assert retrieved_exemption["mcp_id"] == "MCP-001"
    print(f"Retrieved exemption: {retrieved_exemption}")

    # 3. Test GET: Retrieve non-existent exemption
    print("\nTesting GET /mcp/exemptions/999 (Retrieve non-existent)")
    response = client.get("/mcp/exemptions/999")
    assert response.status_code == status.HTTP_404_NOT_FOUND, f"Expected 404, got {response.status_code}"
    assert "not found" in response.json()["detail"]
    print(f"Non-existent exemption retrieval: {response.json()}")

    # 4. Test PUT: Update the exemption
    print(f"\nTesting PUT /mcp/exemptions/{exemption_id} (Update)")
    update_payload = {
        "reason": "Extended maintenance period",
        "expiry_date": "2024-01-31"
    }
    response = client.put(f"/mcp/exemptions/{exemption_id}", json=update_payload)
    assert response.status_code == status.HTTP_200_OK, f"Expected 200, got {response.status_code}"
    updated_exemption = response.json()
    assert updated_exemption["id"] == exemption_id
    assert updated_exemption["reason"] == "Extended maintenance period"
    assert updated_exemption["expiry_date"] == "2024-01-31"
    assert updated_exemption["mcp_id"] == "MCP-001" # Should remain unchanged
    print(f"Updated exemption: {updated_exemption}")

    # 5. Test PUT: Update non-existent exemption
    print("\nTesting PUT /mcp/exemptions/999 (Update non-existent)")
    response = client.put("/mcp/exemptions/999", json={"reason": "test"})
    assert response.status_code == status.HTTP_404_NOT_FOUND, f"Expected 404, got {response.status_code}"
    assert "not found" in response.json()["detail"]
    print(f"Non-existent exemption update: {response.json()}")

    # 6. Test DELETE: Delete the exemption
    print(f"\nTesting DELETE /mcp/exemptions/{exemption_id} (Delete)")
    response = client.delete(f"/mcp/exemptions/{exemption_id}")
    assert response.status_code == status.HTTP_204_NO_CONTENT, f"Expected 204, got {response.status_code}"
    assert response.content == b'', f"Expected empty content, got {response.content}"
    print(f"Exemption {exemption_id} deleted successfully.")

    # 7. Test GET: Verify deletion
    print(f"\nTesting GET /mcp/exemptions/{exemption_id} (Verify deletion)")
    response = client.get(f"/mcp/exemptions/{exemption_id}")
    assert response.status_code == status.HTTP_404_NOT_FOUND, f"Expected 404, got {response.status_code}"
    assert "not found" in response.json()["detail"]
    print(f"Verification: Exemption {exemption_id} is indeed deleted.")

    # 8. Test DELETE: Delete non-existent exemption
    print("\nTesting DELETE /mcp/exemptions/999 (Delete non-existent)")
    response = client.delete("/mcp/exemptions/999")
    assert response.status_code == status.HTTP_404_NOT_FOUND, f"Expected 404, got {response.status_code}"
    assert "not found" in response.json()["detail"]
    print(f"Non-existent exemption deletion: {response.json()}")

    print("\n--- All Acceptance Tests Passed ---")