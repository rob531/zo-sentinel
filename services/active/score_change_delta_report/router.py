# deps: fastapi, sqlalchemy, pydantic
"""Score Change Delta Report -- active service.

Compares consecutive McpLlmAxisScore records for a server, ordered by scored_at,
and returns the delta of p_top, p_critical, and label between each pair.

Public endpoint (auth=public per the directive).
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["score_change_delta_report"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class DeltaItem(BaseModel):
    scored_at: datetime = Field(..., description="Timestamp of the later record")
    axis_name: str = Field(..., description="Axis this delta belongs to")
    p_top_delta: float = Field(..., description="p_top change vs. previous record")
    p_critical_delta: float = Field(..., description="p_critical change vs. previous record")
    label_before: Optional[str] = Field(None, description="Label of the earlier record")
    label_after: Optional[str] = Field(None, description="Label of the later record")

    model_config = {"from_attributes": True}


class ScoreChangeDeltaReport(BaseModel):
    server_id: str = Field(..., description="Server identifier")
    deltas: List[DeltaItem] = Field(default_factory=list, description="Computed deltas")
    score_count: int = Field(0, description="Total score records found for this server")


# ---------------------------------------------------------------------------
# Core data-access routine
# ---------------------------------------------------------------------------

def _get_score_deltas(db: Session, server_id: str) -> ScoreChangeDeltaReport:
    """Query consecutive McpLlmAxisScore rows and compute numeric deltas."""
    rows: List[McpLlmAxisScore] = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at)
        .all()
    )

    if len(rows) < 2:
        return ScoreChangeDeltaReport(server_id=server_id, deltas=[], score_count=len(rows))

    deltas: List[DeltaItem] = []
    for prev, cur in zip(rows, rows[1:]):
        p_top_delta = float(cur.p_top or 0.0) - float(prev.p_top or 0.0)
        p_critical_delta = float(cur.p_critical or 0.0) - float(prev.p_critical or 0.0)

        deltas.append(
            DeltaItem(
                scored_at=cur.scored_at or datetime.utcnow(),
                axis_name=cur.axis_name,
                p_top_delta=p_top_delta,
                p_critical_delta=p_critical_delta,
                label_before=prev.label,
                label_after=cur.label,
            )
        )

    return ScoreChangeDeltaReport(server_id=server_id, deltas=deltas, score_count=len(rows))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/scoring/delta",
    response_model=ScoreChangeDeltaReport,
    summary="Score change delta report for a server",
)
def get_score_change_delta(
    server_id: str = Query(..., description="Server identifier (string)"),
    db: Session = Depends(get_session),
) -> ScoreChangeDeltaReport:
    """
    Return delta values for p_top, p_critical, and label between each
    consecutive pair of McpLlmAxisScore records for the given server,
    ordered by scored_at ascending.

    Returns an empty deltas list if fewer than 2 score records exist.
    """
    return _get_score_deltas(db, server_id)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base, McpLlmAxisScore  # noqa: F401

    # In-memory SQLite for self-test; Base.metadata.create_all handles schema.
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    def get_test_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed three records for server "srv-001"
    records = [
        McpLlmAxisScore(
            server_id="srv-001",
            axis_name="overall_risk",
            scored_at=datetime(2024, 1, 1, 0, 0, 0),
            p_top=0.10,
            p_critical=0.05,
            label="low",
            model_version="v1",
        ),
        McpLlmAxisScore(
            server_id="srv-001",
            axis_name="overall_risk",
            scored_at=datetime(2024, 1, 2, 0, 0, 0),
            p_top=0.20,
            p_critical=0.07,
            label="medium",
            model_version="v1",
        ),
        McpLlmAxisScore(
            server_id="srv-001",
            axis_name="overall_risk",
            scored_at=datetime(2024, 1, 3, 0, 0, 0),
            p_top=0.35,
            p_critical=0.10,
            label="high",
            model_version="v1",
        ),
    ]
    with TestSession() as db:
        for r in records:
            db.add(r)
        db.commit()

    client = TestClient(app)

    # Happy path
    resp = client.get("/api/scoring/delta", params={"server_id": "srv-001"})
    if resp.status_code != 200:
        print(f"FAIL: status {resp.status_code}")
        sys.exit(1)

    payload = resp.json()
    if payload["server_id"] != "srv-001":
        print(f"FAIL: server_id mismatch: {payload['server_id']}")
        sys.exit(1)
    if len(payload["deltas"]) != 2:
        print(f"FAIL: expected 2 deltas, got {len(payload['deltas'])}")
        sys.exit(1)

    d0, d1 = payload["deltas"]
    if abs(d0["p_top_delta"] - 0.10) > 1e-9:
        print(f"FAIL: first p_top_delta expected 0.10, got {d0['p_top_delta']}")
        sys.exit(1)
    if abs(d0["p_critical_delta"] - 0.02) > 1e-9:
        print(f"FAIL: first p_critical_delta expected 0.02, got {d0['p_critical_delta']}")
        sys.exit(1)
    if d0["label_before"] != "low" or d0["label_after"] != "medium":
        print(f"FAIL: first label_delta mismatch: {d0}")
        sys.exit(1)

    if abs(d1["p_top_delta"] - 0.15) > 1e-9:
        print(f"FAIL: second p_top_delta expected 0.15, got {d1['p_top_delta']}")
        sys.exit(1)
    if abs(d1["p_critical_delta"] - 0.03) > 1e-9:
        print(f"FAIL: second p_critical_delta expected 0.03, got {d1['p_critical_delta']}")
        sys.exit(1)

    # Edge case: server with fewer than 2 records returns empty deltas
    resp2 = client.get("/api/scoring/delta", params={"server_id": "srv-no-scores"})
    if resp2.status_code != 200:
        print(f"FAIL: status for no-scores server {resp2.status_code}")
        sys.exit(1)
    if resp2.json()["deltas"] != []:
        print(f"FAIL: expected empty deltas for server with no records")
        sys.exit(1)

    print("PASS")
