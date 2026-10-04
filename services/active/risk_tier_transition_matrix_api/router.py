# deps: fastapi, sqlalchemy, pydantic
"""Risk Tier Transition Matrix API.

GET /api/risk/transition-matrix?days=30
Returns a matrix of server transitions between risk tiers over a time window.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_tier_transition_matrix_api"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class Matrix(BaseModel):
    rows: List[str] = Field(description="Distinct risk tiers used as source rows")
    columns: List[str] = Field(description="Distinct risk tiers used as destination columns")
    cells: List[List[int]] = Field(description="Count matrix indexed by rows × columns")


class Totals(BaseModel):
    from_tier: Dict[str, int] = Field(description="Total exits per source tier")
    to_tier: Dict[str, int] = Field(description="Total entries per destination tier")


class TransitionMatrixResponse(BaseModel):
    days: int = Field(description="Window size in days")
    matrix: Matrix
    totals: Totals


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/risk/transition-matrix",
    response_model=TransitionMatrixResponse,
    summary="Risk tier transition matrix",
)
def get_transition_matrix(
    days: int = Query(default=30, ge=1, description="Number of days to look back"),
    session: Session = Depends(get_session),
) -> TransitionMatrixResponse:
    """
    Return a transition matrix of risk tier movements over the past *days*.
    For each server, the first and last record within the window determine
    the from-tier and to-tier.  Servers with only one record show no transition.
    """
    return _compute_transition_matrix(session=session, days=days)


# ---------------------------------------------------------------------------
# Pure computation (exercised directly by the self-test)
# ---------------------------------------------------------------------------

def _compute_transition_matrix(session: Session, days: int) -> TransitionMatrixResponse:
    """Compute the transition matrix over the given window using app models."""
    cutoff: datetime = datetime.utcnow() - timedelta(days=days)

    # Pull all records within the window, ordered by server then timestamp
    records = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.last_scanned >= cutoff)
        .order_by(McpServerRegistry.server_id, McpServerRegistry.last_scanned)
        .all()
    )

    # Organise records per server
    per_server: Dict[str, List[McpServerRegistry]] = {}
    for rec in records:
        per_server.setdefault(rec.server_id, []).append(rec)

    # Determine transitions: first record's tier → last record's tier
    transition_counts: Dict[tuple, int] = {}
    tiers_set: set = set()
    for _server_id, recs in per_server.items():
        if not recs:
            continue
        from_tier = recs[0].risk_tier
        to_tier = recs[-1].risk_tier
        tiers_set.update([from_tier, to_tier])
        key = (from_tier, to_tier)
        transition_counts[key] = transition_counts.get(key, 0) + 1

    # Build ordered tier lists (None sorts before any string in Python 3)
    tiers = sorted(tiers_set, key=lambda t: t or "")

    # Initialise matrix
    matrix_cells: List[List[int]] = [[0 for _ in tiers] for _ in tiers]

    for i, from_tier in enumerate(tiers):
        for j, to_tier in enumerate(tiers):
            matrix_cells[i][j] = transition_counts.get((from_tier, to_tier), 0)

    # Compute totals
    from_totals: Dict[str, int] = {tier: 0 for tier in tiers}
    to_totals: Dict[str, int] = {tier: 0 for tier in tiers}
    for (from_tier, to_tier), cnt in transition_counts.items():
        from_totals[from_tier] = from_totals.get(from_tier, 0) + cnt
        to_totals[to_tier] = to_totals.get(to_tier, 0) + cnt

    return TransitionMatrixResponse(
        days=days,
        matrix=Matrix(rows=tiers, columns=tiers, cells=matrix_cells),
        totals=Totals(from_tier=from_totals, to_tier=to_totals),
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In-memory SQLite for self-test; the module's own data access stays
    # via app.db import get_session (overridden here for the test).
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    TestSessionLocal = sessionmaker(bind=engine)

    from app.models import Base
    Base.metadata.create_all(engine)

    def _override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override_get_session

    now = datetime.utcnow()
    with TestSessionLocal() as db:
        # Server 1: low → medium
        db.add(McpServerRegistry(server_id="s1", risk_tier="low",
                               last_scanned=now - timedelta(days=2)))
        db.add(McpServerRegistry(server_id="s1", risk_tier="medium",
                               last_scanned=now - timedelta(days=1)))
        # Server 2: medium → high
        db.add(McpServerRegistry(server_id="s2", risk_tier="medium",
                               last_scanned=now - timedelta(days=2)))
        db.add(McpServerRegistry(server_id="s2", risk_tier="high",
                               last_scanned=now - timedelta(days=1)))
        # Server 3: low → low (no change)
        db.add(McpServerRegistry(server_id="s3", risk_tier="low",
                               last_scanned=now - timedelta(days=2)))
        db.add(McpServerRegistry(server_id="s3", risk_tier="low",
                               last_scanned=now - timedelta(days=1)))
        # Server 4: high → high (no change)
        db.add(McpServerRegistry(server_id="s4", risk_tier="high",
                               last_scanned=now - timedelta(days=2)))
        db.add(McpServerRegistry(server_id="s4", risk_tier="high",
                               last_scanned=now - timedelta(days=1)))
        # Server 5: medium → low
        db.add(McpServerRegistry(server_id="s5", risk_tier="medium",
                               last_scanned=now - timedelta(days=2)))
        db.add(McpServerRegistry(server_id="s5", risk_tier="low",
                               last_scanned=now - timedelta(days=1)))
        db.commit()

    client = TestClient(test_app)

    # Happy path
    resp = client.get("/api/risk/transition-matrix?days=2")
    if resp.status_code != 200:
        print(f"FAIL: status {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()

    if data["days"] != 2:
        print(f"FAIL: expected days=2, got {data['days']}")
        sys.exit(1)

    rows = data["matrix"]["rows"]
    cols = data["matrix"]["columns"]
    if sorted(rows) != rows:
        print("FAIL: rows not sorted")
        sys.exit(1)
    if sorted(cols) != cols:
        print("FAIL: columns not sorted")
        sys.exit(1)

    # low → medium = 1
    i = rows.index("low")
    j = cols.index("medium")
    if data["matrix"]["cells"][i][j] != 1:
        print(f"FAIL: expected low→medium=1, got {data['matrix']['cells'][i][j]}")
        sys.exit(1)

    # medium → low = 1
    i = rows.index("medium")
    j = cols.index("low")
    if data["matrix"]["cells"][i][j] != 1:
        print(f"FAIL: expected medium→low=1, got {data['matrix']['cells'][i][j]}")
        sys.exit(1)

    # medium → high = 1
    i = rows.index("medium")
    j = cols.index("high")
    if data["matrix"]["cells"][i][j] != 1:
        print(f"FAIL: expected medium→high=1, got {data['matrix']['cells'][i][j]}")
        sys.exit(1)

    # Default days parameter
    resp_default = client.get("/api/risk/transition-matrix")
    if resp_default.status_code != 200:
        print(f"FAIL: default days returned {resp_default.status_code}")
        sys.exit(1)
    if resp_default.json()["days"] != 30:
        print(f"FAIL: expected default days=30, got {resp_default.json()['days']}")
        sys.exit(1)

    # Out-of-range days → 422
    resp_422 = client.get("/api/risk/transition-matrix?days=0")
    if resp_422.status_code != 422:
        print(f"FAIL: expected 422 for days=0, got {resp_422.status_code}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)
