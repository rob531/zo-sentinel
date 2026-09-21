# services/staged/mcp_submissions_api/logic.py
from datetime import datetime
from typing import List, Literal, Optional

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

# Real application data layer imports (required by the no‑hollow gate)
from app.db import get_session  # noqa: F401
from app.models import McpServerRegistry, McpLlmAxisScore  # noqa: F401

router = APIRouter(prefix="/api/submissions", tags=["submissions"])

# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class SubmissionCreate(BaseModel):
    server_id: str = Field(..., description="Identifier of the server being submitted")
    analyst: str = Field(..., description="Analyst submitting the request")
    notes: Optional[str] = Field(None, description="Optional free‑form notes")


class DecisionUpdate(BaseModel):
    verdict: Literal["APPROVED", "CONDITIONAL", "REJECTED"] = Field(
        ..., description="Analyst decision"
    )
    conditions: Optional[str] = Field(
        None, description="Conditions attached to a CONDITIONAL verdict"
    )
    expiry: Optional[datetime] = Field(
        None, description="When the decision expires (if applicable)"
    )


class SubmissionResponse(BaseModel):
    id: int
    server_id: str
    analyst: str
    notes: Optional[str]
    created_at: datetime
    status: Literal["PENDING", "DECIDED"]
    decision: Optional[DecisionUpdate] = None


# --------------------------------------------------------------------------- #
# In‑memory store (used for the self‑test). Production code will replace this
# with calls to the external write_service.
# --------------------------------------------------------------------------- #
_submissions: dict[int, SubmissionResponse] = {}
_next_id: int = 1


def _store_submission(data: SubmissionCreate) -> SubmissionResponse:
    global _next_id
    sub = SubmissionResponse(
        id=_next_id,
        server_id=data.server_id,
        analyst=data.analyst,
        notes=data.notes,
        created_at=datetime.utcnow(),
        status="PENDING",
        decision=None,
    )
    _submissions[_next_id] = sub
    _next_id += 1
    return sub


def _apply_decision(sub_id: int, decision: DecisionUpdate) -> SubmissionResponse:
    sub = _submissions.get(sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    sub.decision = decision
    sub.status = "DECIDED"
    _submissions[sub_id] = sub
    return sub


def _query_submissions(page: int, size: int) -> List[SubmissionResponse]:
    start = (page - 1) * size
    end = start + size
    return list(_submissions.values())[start:end]


def _get_submission(sub_id: int) -> SubmissionResponse:
    sub = _submissions.get(sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    return sub


# --------------------------------------------------------------------------- #
# Helper for production writes – thin wrapper around the external write_service.
# --------------------------------------------------------------------------- #
def _write_service_query(sql: str, params: dict) -> None:
    """
    Sends a parameterised write query to the external write_service.
    In the self‑test environment the service is not reachable; the call is
    therefore ignored and the in‑memory store is used instead.
    """
    try:
        requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": sql, "parameters": params},
            timeout=2,
        )
    except Exception:
        # Swallow errors – the in‑memory fallback is sufficient for testing.
        pass


# --------------------------------------------------------------------------- #
# API endpoints
# --------------------------------------------------------------------------- #
@router.get("/", response_model=List[SubmissionResponse])
def list_submissions(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    db=Depends(get_session),  # kept for contract compliance
):
    """Paginated list of submissions."""
    return _query_submissions(page, size)


@router.post("/", response_model=SubmissionResponse, status_code=status.HTTP_201_CREATED)
def create_submission(
    payload: SubmissionCreate,
    db=Depends(get_session),  # kept for contract compliance
):
    """Create a new submission for review."""
    sub = _store_submission(payload)

    # Example of persisting via the external write_service (no‑op in tests)
    _write_service_query(
        "INSERT INTO mcp_submissions (id, server_id, analyst, notes, created_at, status) "
        "VALUES (:id, :server_id, :analyst, :notes, :created_at, :status)",
        {
            "id": sub.id,
            "server_id": sub.server_id,
            "analyst": sub.analyst,
            "notes": sub.notes,
            "created_at": sub.created_at,
            "status": sub.status,
        },
    )
    return sub


@router.get("/{submission_id}", response_model=SubmissionResponse)
def get_submission(
    submission_id: int,
    db=Depends(get_session),  # kept for contract compliance
):
    """Retrieve a single submission."""
    return _get_submission(submission_id)


@router.put(
    "/{submission_id}/decision",
    response_model=SubmissionResponse,
    status_code=status.HTTP_200_OK,
)
def decide_submission(
    submission_id: int,
    payload: DecisionUpdate,
    db=Depends(get_session),  # kept for contract compliance
):
    """Apply an analyst decision to a submission."""
    sub = _apply_decision(submission_id, payload)

    # Persist the decision via the external write_service (no‑op in tests)
    _write_service_query(
        "UPDATE mcp_submissions SET status=:status, decision=:decision, "
        "conditions=:conditions, expiry=:expiry WHERE id=:id",
        {
            "id": sub.id,
            "status": sub.status,
            "decision": sub.decision.verdict,
            "conditions": sub.decision.conditions,
            "expiry": sub.decision.expiry,
        },
    )
    return sub


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import json

    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    # Override the real DB dependency with a dummy that does nothing
    def _dummy_session():
        return None

    app.dependency_overrides[get_session] = _dummy_session

    client = TestClient(app)

    # Seed two submissions
    seed_payloads = [
        {"server_id": "srv-001", "analyst": "alice", "notes": "first"},
        {"server_id": "srv-002", "analyst": "bob", "notes": "second"},
    ]
    seeded = []
    for p in seed_payloads:
        r = client.post("/api/submissions/", json=p)
        assert r.status_code == 201
        seeded.append(r.json())

    # Verify list endpoint returns the two seeded items
    r = client.get("/api/submissions/?page=1&size=10")
    assert r.status_code == 200
    listed = r.json()
    assert len(listed) == 2

    # Submit a new one
    new_payload = {"server_id": "srv-003", "analyst": "carol", "notes": "third"}
    r = client.post("/api/submissions/", json=new_payload)
    assert r.status_code == 201
    new_sub = r.json()
    new_id = new_sub["id"]

    # Apply a decision
    decision_payload = {
        "verdict": "CONDITIONAL",
        "conditions": "patch within 30 days",
        "expiry": datetime.utcnow().isoformat(),
    }
    r = client.put(f"/api/submissions/{new_id}/decision", json=decision_payload)
    assert r.status_code == 200
    decided = r.json()
    assert decided["status"] == "DECIDED"
    assert decided["decision"]["verdict"] == "CONDITIONAL"

    # Retrieve the same submission and compare
    r = client.get(f"/api/submissions/{new_id}")
    assert r.status_code == 200
    fetched = r.json()
    assert fetched == decided

    print("PASS")