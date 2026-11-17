import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

router = APIRouter(prefix="/api")


@router.get(
    "/disputes/shift-ledger",
    response_model=Dict[str, Any],
    summary="Summarise risk tier shifts for disputed scores",
)
def shift_ledger(
    status: Optional[str] = Query(default=None, description="Filter by dispute status"),
    server_id: Optional[str] = Query(default=None, description="Filter by server ID"),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description="Maximum number of shift records to return",
    ),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """
    Return a summary of how LLM‑scored risk tiers moved for servers that have a
    pending or resolved score dispute.
    """
    # Base query joining disputes with server registry
    base_q = (
        db.query(McpScoreDispute, McpServerRegistry)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .filter()
    )

    if status is not None:
        base_q = base_q.filter(McpScoreDispute.status == status)
    if server_id is not None:
        base_q = base_q.filter(McpScoreDispute.server_id == server_id)

    # Total count (before limit)
    total = base_q.count()

    # Status counts across the filtered set (ignoring limit)
    status_counts_sub = (
        db.query(McpScoreDispute.status, func.count())
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .filter()
    )
    if status is not None:
        status_counts_sub = status_counts_sub.filter(McpScoreDispute.status == status)
    if server_id is not None:
        status_counts_sub = status_counts_sub.filter(McpScoreDispute.server_id == server_id)

    status_counts_raw = status_counts_sub.group_by(McpScoreDispute.status).all()
    status_counts: Dict[str, int] = {s: c for s, c in status_counts_raw}

    # Retrieve shift rows with limit
    rows = (
        base_q.order_by(McpScoreDispute.created_at.desc())
        .limit(limit)
        .all()
    )

    shifts: List[Dict[str, Any]] = []
    for dispute, server in rows:
        shifts.append(
            {
                "server_id": dispute.server_id,
                "name": server.name,
                "current_tier": server.risk_tier,
                "proposed_overall_risk": dispute.proposed_overall_risk,
                "status": dispute.status,
                "reason_category": dispute.reason_category,
                "created_at": dispute.created_at.isoformat()
                if isinstance(dispute.created_at, datetime)
                else str(dispute.created_at),
                "resolved_at": dispute.resolved_at.isoformat()
                if isinstance(dispute.resolved_at, datetime)
                else None,
            }
        )

    return {"status_counts": status_counts, "shifts": shifts, "total": total}


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build a temporary FastAPI app with an in‑memory SQLite DB
    test_app = FastAPI()
    test_app.include_router(router)

    # Create an SQLite engine that lives only in memory
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables for the two models we need
    from app.db import Base  # Base is the declarative base used by the models

    Base.metadata.create_all(bind=engine)

    # Dependency override to use the in‑memory session
    def get_test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = get_test_session

    # Seed data
    with SessionLocal() as db:
        # Servers
        server_a = McpServerRegistry(
            server_id="srv-001",
            name="Alpha",
            risk_tier="high",
        )
        server_b = McpServerRegistry(
            server_id="srv-002",
            name="Beta",
            risk_tier="medium",
        )
        db.add_all([server_a, server_b])

        # Disputes
        dispute1 = McpScoreDispute(
            id=1,
            server_id="srv-001",
            proposed_overall_risk="low",
            reason_category="false_positive",
            status="pending",
            created_at=datetime.utcnow(),
            explanation="test",
            submitted_by="tester",
        )
        dispute2 = McpScoreDispute(
            id=2,
            server_id="srv-001",
            proposed_overall_risk="medium",
            reason_category="policy_change",
            status="resolved",
            created_at=datetime.utcnow(),
            resolved_at=datetime.utcnow(),
            explanation="test",
            submitted_by="tester",
        )
        dispute3 = McpScoreDispute(
            id=3,
            server_id="srv-002",
            proposed_overall_risk="high",
            reason_category="risk_reassessment",
            status="rejected",
            created_at=datetime.utcnow(),
            explanation="test",
            submitted_by="tester",
        )
        db.add_all([dispute1, dispute2, dispute3])
        db.commit()

    client = TestClient(test_app)

    # Basic request – no filters
    resp = client.get("/api/disputes/shift-ledger")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["total"] == 3, f"Expected total 3, got {data['total']}"
    # Verify that a known server's current tier appears
    found = any(
        shift["server_id"] == "srv-001" and shift["current_tier"] == "high"
        for shift in data["shifts"]
    )
    assert found, "Current tier for srv-001 not found in shifts"

    # Status filter narrows counts
    resp_pending = client.get("/api/disputes/shift-ledger", params={"status": "pending"})
    assert resp_pending.status_code == 200
    pending_data = resp_pending.json()
    assert pending_data["status_counts"] == {"pending": 1}, "Status filter did not narrow counts"

    print("PASS")