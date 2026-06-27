# mcp_decision_submission_api.py

from fastapi import APIRouter, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from enum import Enum
from datetime import date, datetime
from typing import Optional, Dict
import uuid

# --- Pydantic Models ---

class DecisionStatus(str, Enum):
    """
    Enum for possible MCP decision statuses.
    """
    APPROVED = "APPROVED"
    CONDITIONAL = "CONDITIONAL"
    REJECTED = "REJECTED"

class MCPDecisionCreate(BaseModel):
    """
    Request model for submitting a new MCP decision.
    """
    mcp_id: int = Field(..., description="Unique identifier for the MCP.")
    decision: DecisionStatus = Field(..., description="The decision made for the MCP.")
    analyst_id: str = Field(..., description="Identifier of the analyst making the decision.")
    notes: Optional[str] = Field(None, description="Additional notes regarding the decision.")
    expiry_date: Optional[date] = Field(None, description="Optional expiry date for the decision.")

class MCPDecisionResponse(MCPDecisionCreate):
    """
    Response model for an MCP decision record, including generated fields.
    """
    id: str = Field(..., description="Unique identifier for the decision record.")
    created_at: datetime = Field(..., description="Timestamp when the decision was first created.")
    updated_at: datetime = Field(..., description="Timestamp when the decision was last updated.")

# --- Database Simulation/Service ---
# In a real application, this would be a service layer interacting with a database
# (e.g., using SQLAlchemy with a PostgreSQL backend).
# For this task, we simulate an in-memory database with ON CONFLICT DO UPDATE logic.

class MockMCPDecisionService:
    """
    Simulates a service for interacting with the 'mcp_decisions' table.
    Implements the logic for 'INSERT ... ON CONFLICT (mcp_id) DO UPDATE'.
    """
    def __init__(self):
        # Stores decision records, indexed by their unique 'id'
        self._decisions_by_id: Dict[str, MCPDecisionResponse] = {}
        # Secondary index to quickly find a decision by 'mcp_id'
        self._decision_id_by_mcp_id: Dict[int, str] = {}

    def _generate_id(self) -> str:
        """Generates a unique ID for a new decision record."""
        return str(uuid.uuid4())

    def _now(self) -> datetime:
        """Returns the current UTC datetime."""
        return datetime.now()

    def create_or_update_decision(self, decision_data: MCPDecisionCreate) -> MCPDecisionResponse:
        """
        Creates a new decision record or updates an existing one for a given MCP.
        This method simulates the 'ON CONFLICT (mcp_id) DO UPDATE' behavior.
        """
        existing_decision_id = self._decision_id_by_mcp_id.get(decision_data.mcp_id)
        current_time = self._now()

        if existing_decision_id:
            # Simulate UPDATE: A decision for this mcp_id already exists.
            existing_record = self._decisions_by_id[existing_decision_id]
            updated_record = existing_record.copy(
                update={
                    "decision": decision_data.decision,
                    "analyst_id": decision_data.analyst_id,
                    "notes": decision_data.notes,
                    "expiry_date": decision_data.expiry_date,
                    "updated_at": current_time,
                }
            )
            self._decisions_by_id[existing_decision_id] = updated_record
            return updated_record
        else:
            # Simulate INSERT: No decision for this mcp_id exists.
            new_id = self._generate_id()
            new_record = MCPDecisionResponse(
                id=new_id,
                created_at=current_time,
                updated_at=current_time,
                **decision_data.dict()
            )
            self._decisions_by_id[new_id] = new_record
            self._decision_id_by_mcp_id[decision_data.mcp_id] = new_id
            return new_record

    def get_decision_by_mcp_id(self, mcp_id: int) -> Optional[MCPDecisionResponse]:
        """
        Retrieves a decision record by its MCP ID.
        Used primarily for testing and verification.
        """
        decision_id = self._decision_id_by_mcp_id.get(mcp_id)
        if decision_id:
            return self._decisions_by_id.get(decision_id)
        return None

# Initialize our mock decision service
decision_service = MockMCPDecisionService()

# --- FastAPI Router ---

router = APIRouter()

@router.post(
    "/mcp/decisions",
    response_model=MCPDecisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit or update an MCP decision",
    description="Allows analysts to submit new decisions or update existing ones for MCPs. "
                "If a decision for the given `mcp_id` already exists, it will be updated "
                "(simulating `ON CONFLICT (mcp_id) DO UPDATE`)."
)
async def submit_mcp_decision(decision_data: MCPDecisionCreate):
    """
    Handles the submission of an MCP decision.
    If an existing decision for the `mcp_id` is found, it updates the record.
    Otherwise, it creates a new decision record.
    """
    try:
        # Call the service layer to handle the business logic and persistence
        new_or_updated_decision = decision_service.create_or_update_decision(decision_data)
        return new_or_updated_decision
    except Exception as e:
        # In a real application, detailed logging would be performed here.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process MCP decision: {e}"
        )

# --- Acceptance Test (`__main__` block) ---

if __name__ == "__main__":
    from fastapi.testclient import TestClient

    # Create a FastAPI app instance and include the router
    app = FastAPI(title="MCP Decision API")
    app.include_router(router)

    # Create a TestClient for making requests
    client = TestClient(app)

    print("--- Running Acceptance Tests for MCP Decision API ---")

    # --- Test Case 1: Post a new valid decision ---
    mcp_id_1 = 101
    initial_decision_payload = {
        "mcp_id": mcp_id_1,
        "decision": "APPROVED",
        "analyst_id": "analyst_A",
        "notes": "Initial approval for project X, pending final review.",
        "expiry_date": "2024-12-31"
    }

    print(f"\n[TEST 1] Posting initial decision for MCP ID: {mcp_id_1}...")
    response = client.post("/mcp/decisions", json=initial_decision_payload)

    # Assert 200 OK response
    assert response.status_code == status.HTTP_200_OK, \
        f"Expected status 200 for initial POST, got {response.status_code}"
    initial_record = response.json()
    print(f"  Response: {initial_record}")

    # Assert response content
    assert initial_record["mcp_id"] == mcp_id_1
    assert initial_record["decision"] == "APPROVED"
    assert initial_record["analyst_id"] == "analyst_A"
    assert "id" in initial_record
    assert "created_at" in initial_record
    assert "updated_at" in initial_record
    initial_record_id = initial_record["id"]
    initial_created_at = datetime.fromisoformat(initial_record["created_at"])
    initial_updated_at = datetime.fromisoformat(initial_record["updated_at"])

    # Assert record is present in the simulated database
    db_record_after_create = decision_service.get_decision_by_mcp_id(mcp_id_1)
    assert db_record_after_create is not None, "Record not found in mock DB after creation."
    assert db_record_after_create.id == initial_record_id
    assert db_record_after_create.decision.value == initial_record["decision"]
    print(f"  Verification: Record with ID '{db_record_after_create.id}' created and found in DB.")

    # --- Test Case 2: Post an update to the same MCP ---
    updated_decision_payload = {
        "mcp_id": mcp_id_1,
        "decision": "CONDITIONAL",
        "analyst_id": "analyst_B", # Changed analyst
        "notes": "Conditional approval pending documentation from client.",
        "expiry_date": "2025-06-30" # Changed expiry date
    }

    print(f"\n[TEST 2] Posting update decision for MCP ID: {mcp_id_1}...")
    response = client.post("/mcp/decisions", json=updated_decision_payload)

    # Assert 200 OK response
    assert response.status_code == status.HTTP_200_OK, \
        f"Expected status 200 for update POST, got {response.status_code}"
    updated_record = response.json()
    print(f"  Response: {updated_record}")

    # Assert record is updated
    assert updated_record["mcp_id"] == mcp_id_1
    assert updated_record["decision"] == "CONDITIONAL" # Decision should be updated
    assert updated_record["analyst_id"] == "analyst_B" # Analyst should be updated
    assert updated_record["notes"] == "Conditional approval pending documentation from client."
    assert updated_record["expiry_date"] == "2025-06-30"
    assert updated_record["id"] == initial_record_id # ID should remain the same
    assert updated_record["created_at"] == initial_record["created_at"] # Created_at should remain the same
    # Updated_at should be newer than the initial updated_at
    assert datetime.fromisoformat(updated_record["updated_at"]) > initial_updated_at

    # Assert record is updated in the simulated database
    db_record_after_update = decision_service.get_decision_by_mcp_id(mcp_id_1)
    assert db_record_after_update is not None, "Record not found in mock DB after update."
    assert db_record_after_update.id == initial_record_id
    assert db_record_after_update.decision.value == "CONDITIONAL"
    assert db_record_after_update.analyst_id == "analyst_B"
    assert db_record_after_update.notes == "Conditional approval pending documentation from client."
    assert db_record_after_update.expiry_date == date(2025, 6, 30)
    assert db_record_after_update.updated_at > initial_updated_at
    print(f"  Verification: Record with ID '{db_record_after_update.id}' successfully updated and verified in DB.")

    # --- Test Case 3: Post another new decision for a different MCP ---
    mcp_id_2 = 102
    new_decision_payload = {
        "mcp_id": mcp_id_2,
        "decision": "REJECTED",
        "analyst_id": "analyst_C",
        "notes": "Rejected due to budget constraints."
    }
    print(f"\n[TEST 3] Posting new decision for MCP ID: {mcp_id_2}...")
    response = client.post("/mcp/decisions", json=new_decision_payload)
    assert response.status_code == status.HTTP_200_OK, \
        f"Expected status 200 for new MCP {mcp_id_2} POST, got {response.status_code}"
    new_record_2 = response.json()
    print(f"  Response: {new_record_2}")
    assert new_record_2["mcp_id"] == mcp_id_2
    assert new_record_2["decision"] == "REJECTED"
    db_record_2 = decision_service.get_decision_by_mcp_id(mcp_id_2)
    assert db_record_2 is not None
    assert db_record_2.id == new_record_2["id"]
    print(f"  Verification: Record for MCP ID '{mcp_id_2}' successfully created.")


    print("\n--- All acceptance tests passed! ---")
    print("PASS")