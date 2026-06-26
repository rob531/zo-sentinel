# mcp_attestations_dashboard_api.py

from datetime import datetime, date
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

# --- Pydantic Models ---

# Model for a single attestation record
class Attestation(BaseModel):
    """
    Represents a single attestation record from the mcp_attestations table.
    """
    id: int = Field(..., description="Internal database ID for the attestation record.")
    attestation_id: str = Field(..., description="Unique identifier for the attestation itself (e.g., a UUID or hash).")
    attestation_type: str = Field(..., description="Type of attestation (e.g., 'compliance', 'security', 'audit').")
    status: str = Field(..., description="Current status of the attestation (e.g., 'pending', 'approved', 'rejected', 'failed').")
    timestamp: datetime = Field(..., description="Timestamp when the attestation event occurred or was recorded.")
    source: str = Field(..., description="Source system or entity that generated the attestation.")
    data: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary JSON data associated with the attestation.")
    created_at: datetime = Field(..., description="Timestamp when the record was created in the database.")
    updated_at: datetime = Field(..., description="Timestamp when the record was last updated in the database.")

    class Config:
        orm_mode = True # Enables ORM mode for compatibility with SQLAlchemy models

# Model for query parameters for listing attestations
class AttestationQueryParams(BaseModel):
    """
    Defines the available query parameters for filtering and paginating attestation lists.
    """
    attestation_type: Optional[str] = Query(None, description="Filter by attestation type (e.g., 'compliance', 'security').")
    status: Optional[str] = Query(None, description="Filter by attestation status (e.g., 'pending', 'approved').")
    source: Optional[str] = Query(None, description="Filter by attestation source system or entity.")
    start_date: Optional[date] = Query(None, description="Filter attestations from this date onwards (inclusive). Format: YYYY-MM-DD.")
    end_date: Optional[date] = Query(None, description="Filter attestations up to this date (inclusive). Format: YYYY-MM-DD.")
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of attestations to return per page.")
    offset: int = Query(0, ge=0, description="Number of attestations to skip (for pagination).")

# Model for paginated response structure
class PaginatedAttestationsResponse(BaseModel):
    """
    Standard response structure for paginated lists of attestations.
    """
    total: int = Field(..., description="Total number of attestations matching the criteria.")
    limit: int = Field(..., description="The maximum number of items requested in this page.")
    offset: int = Field(..., description="The number of items skipped to reach this page.")
    items: List[Attestation] = Field(..., description="List of attestation records for the current page.")

# Model for attestation summary statistics
class AttestationSummary(BaseModel):
    """
    Provides aggregated summary data for attestations.
    """
    total_attestations: int = Field(..., description="Total number of attestations matching the applied filters.")
    status_counts: Dict[str, int] = Field(..., description="Counts of attestations grouped by their status.")
    type_counts: Dict[str, int] = Field(..., description="Counts of attestations grouped by their type.")
    # Additional summary fields can be added here as needed (e.g., source_counts, date_range_counts)

# --- Database Dependency (Placeholder) ---
# In a real application, this would integrate with a database ORM like SQLAlchemy.
# For this example, we use a simple in-memory mock database.

class MockDatabase:
    """
    A simple in-memory mock database for demonstration purposes.
    In a production environment, this would be replaced by actual database interaction.
    """
    def __init__(self):
        self._data: List[Attestation] = []
        self._next_id = 1
        self._seed_data()

    def _seed_data(self):
        """Populates the mock database with some sample attestation data."""
        now = datetime.utcnow()
        self.create_attestation(
            attestation_id="attest-comp-001",
            attestation_type="compliance",
            status="approved",
            timestamp=now.replace(day=1),
            source="system_A",
            data={"rule_id": "C-001", "passed": True}
        )
        self.create_attestation(
            attestation_id="attest-sec-002",
            attestation_type="security",
            status="pending",
            timestamp=now.replace(day=2),
            source="system_B",
            data={"vulnerability_scan_id": "VS-001", "severity": "high"}
        )
        self.create_attestation(
            attestation_id="attest-comp-003",
            attestation_type="compliance",
            status="rejected",
            timestamp=now.replace(day=3),
            source="user_input",
            data={"reason": "missing documentation"}
        )
        self.create_attestation(
            attestation_id="attest-audit-004",
            attestation_type="audit",
            status="approved",
            timestamp=now.replace(day=4),
            source="system_C",
            data={"audit_report_id": "AR-001"}
        )
        self.create_attestation(
            attestation_id="attest-sec-005",
            attestation_type="security",
            status="failed",
            timestamp=now.replace(day=5),
            source="system_B",
            data={"vulnerability_scan_id": "VS-002", "severity": "critical"}
        )
        self.create_attestation(
            attestation_id="attest-comp-006",
            attestation_type="compliance",
            status="pending",
            timestamp=now.replace(day=6),
            source="system_A",
            data={"rule_id": "C-002", "passed": False}
        )

    def create_attestation(self, **kwargs) -> Attestation:
        """Adds a new attestation record to the mock database."""
        now = datetime.utcnow()
        attestation = Attestation(
            id=self._next_id,
            created_at=now,
            updated_at=now,
            **kwargs
        )
        self._data.append(attestation)
        self._next_id += 1
        return attestation

    def get_attestations(self, params: AttestationQueryParams) -> List[Attestation]:
        """
        Retrieves attestations based on the provided query parameters.
        Applies filtering but not pagination here, as pagination is handled by the endpoint.
        """
        filtered_data = self._data

        if params.attestation_type:
            filtered_data = [a for a in filtered_data if a.attestation_type == params.attestation_type]
        if params.status:
            filtered_data = [a for a in filtered_data if a.status == params.status]
        if params.source:
            filtered_data = [a for a in filtered_data if a.source == params.source]
        if params.start_date:
            filtered_data = [a for a in filtered_data if a.timestamp.date() >= params.start_date]
        if params.end_date:
            filtered_data = [a for a in filtered_data if a.timestamp.date() <= params.end_date]

        # Sort by timestamp for consistent pagination
        filtered_data.sort(key=lambda x: x.timestamp, reverse=True)
        return filtered_data

    def get_attestation_by_id(self, attestation_id: int) -> Optional[Attestation]:
        """Retrieves a single attestation by its internal database ID."""
        for attestation in self._data:
            if attestation.id == attestation_id:
                return attestation
        return None

    def get_attestation_by_unique_id(self, unique_attestation_id: str) -> Optional[Attestation]:
        """Retrieves a single attestation by its unique business attestation_id."""
        for attestation in self._data:
            if attestation.attestation_id == unique_attestation_id:
                return attestation
        return None

# Initialize the mock database instance
mock_db = MockDatabase()

def get_db():
    """
    FastAPI dependency that provides a database session/connection.
    In a real application, this would yield a SQLAlchemy Session or similar.
    """
    yield mock_db

# --- FastAPI Router ---

mcp_attestations_router = APIRouter(
    prefix="/mcp_attestations",
    tags=["MCP Attestations Dashboard"],
    responses={404: {"description": "Not found"}},
)

@mcp_attestations_router.get(
    "/",
    response_model=PaginatedAttestationsResponse,
    summary="Retrieve a list of MCP Attestations",
    description="""
    Fetches a paginated list of attestation records.
    Supports filtering by `attestation_type`, `status`, `source`, and a `start_date`/`end_date` range.
    Results are ordered by timestamp (most recent first).
    """
)
async def list_attestations(
    params: AttestationQueryParams = Depends(),
    db: MockDatabase = Depends(get_db)
):
    """
    Endpoint to retrieve a list of attestation records.
    """
    all_matching_attestations = db.get_attestations(params)
    total_count = len(all_matching_attestations)

    # Apply pagination based on offset and limit
    paginated_attestations = all_matching_attestations[params.offset : params.offset + params.limit]

    return PaginatedAttestationsResponse(
        total=total_count,
        limit=params.limit,
        offset=params.offset,
        items=paginated_attestations
    )

@mcp_attestations_router.get(
    "/{attestation_id}",
    response_model=Attestation,
    summary="Retrieve a single MCP Attestation by internal ID",
    description="Fetches a specific attestation record using its internal database ID."
)
async def get_attestation_by_id(
    attestation_id: int = Field(..., description="The internal database ID of the attestation."),
    db: MockDatabase = Depends(get_db)
):
    """
    Endpoint to retrieve a single attestation by its internal database ID.
    """
    attestation = db.get_attestation_by_id(attestation_id)
    if not attestation:
        raise HTTPException(status_code=404, detail=f"Attestation with ID {attestation_id} not found")
    return attestation

@mcp_attestations_router.get(
    "/by_unique_id/{unique_attestation_id}",
    response_model=Attestation,
    summary="Retrieve a single MCP Attestation by its unique attestation ID",
    description="Fetches a specific attestation record using its unique business attestation identifier (e.g., UUID or custom ID)."
)
async def get_attestation_by_unique_id(
    unique_attestation_id: str = Field(..., description="The unique business identifier of the attestation."),
    db: MockDatabase = Depends(get_db)
):
    """
    Endpoint to retrieve a single attestation by its unique business attestation_id.
    """
    attestation = db.get_attestation_by_unique_id(unique_attestation_id)
    if not attestation:
        raise HTTPException(status_code=404, detail=f"Attestation with unique ID '{unique_attestation_id}' not found")
    return attestation

@mcp_attestations_router.get(
    "/summary/",
    response_model=AttestationSummary,
    summary="Get a summary of MCP Attestations",
    description="""
    Provides aggregated summary statistics for attestations, including total counts
    and counts grouped by status and type.
    Supports the same filtering parameters as the list endpoint.
    """
)
async def get_attestations_summary(
    params: AttestationQueryParams = Depends(), # Use query params for filtering summary
    db: MockDatabase = Depends(get_db)
):
    """
    Endpoint to get a summary of attestation data.
    """
    # Retrieve all matching attestations (without pagination) to calculate summary
    all_matching_attestations = db.get_attestations(params)

    status_counts: Dict[str, int] = {}
    type_counts: Dict[str, int] = {}

    for attestation in all_matching_attestations:
        status_counts[attestation.status] = status_counts.get(attestation.status, 0) + 1
        type_counts[attestation.attestation_type] = type_counts.get(attestation.attestation_type, 0) + 1

    return AttestationSummary(
        total_attestations=len(all_matching_attestations),
        status_counts=status_counts,
        type_counts=type_counts
    )

# --- Main Application Integration Example ---
# To integrate this API into your main FastAPI application, you would typically
# import `mcp_attestations_router` into your main application file (e.g., `main.py` or `app.py`)
# and include it using `app.include_router()`.

# Example of how to integrate in your main application file:
"""
# main.py
from fastapi import FastAPI
from mcp_attestations_dashboard_api import mcp_attestations_router

app = FastAPI(
    title="Zo-Sentinel API",
    description="API for Zo-Sentinel platform, including MCP Attestations.",
    version="0.1.0",
)

# Include the attestations router
app.include_router(mcp_attestations_router)

@app.get("/")
async def root():
    return {"message": "Welcome to Zo-Sentinel API"}

# To run this example:
# 1. Save the code above as `mcp_attestations_dashboard_api.py`
# 2. Create a `main.py` file with the content shown above (uncommented).
# 3. Run from your terminal: `uvicorn main:app --reload`
# 4. Access the API documentation at: `http://127.0.0.1:8000/docs`
"""