# services/staged/score_change_timeline/contract.py
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Real data layer imports (must remain unchanged for production)
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, Base  # type: ignore

router = APIRouter(prefix="/api")


class ChangeItem(BaseModel):
    date: datetime = Field(..., description="Date of the later observation")
    axis: str = Field(..., description="Axis name")
    p_top_before: float = Field(..., description="p_top before change")
    p_top_after: float = Field(..., description="p_top after change")
    delta: float = Field(..., description="p_top_after - p_top_before")
    tier_before: Optional[str] = Field(None, description="Risk tier before change")
    tier_after: Optional[str] = Field(None, description="Risk tier after change")


class TimelineResponse(BaseModel):
    server_id: int
    days: int
    changes: List[ChangeItem]


@router.get(
    "/scoring/timeline",
    response_model=TimelineResponse,
    summary="Score change timeline for a server",
)
def get_score_change_timeline(
    server_id: int = Query(..., description="Server identifier"),
    days: int = Query(..., ge=1, description="Number of days to look back"),
    db: Session = Depends(get_session),
):
    # Verify server exists
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    cutoff = datetime.utcnow() - timedelta(days=days)

    scores = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.observed_at >= cutoff,
        )
        .order_by(McpLlmAxisScore.observed_at)
        .all()
    )

    # Group scores by axis
    axis_groups = {}
    for s in scores:
        axis_groups.setdefault(s.axis, []).append(s)

    changes: List[ChangeItem] = []

    for axis, records in axis_groups.items():
        # Need at least two records to compute a change
        if len(records) < 2:
            continue
        for before, after in zip(records, records[1:]):
            delta = after.p_top - before.p_top
            # Tier information: attempt to pull from server.historical_tier if present
            tier_before = getattr(server, "historical_tier", None)
            tier_after = getattr(server, "historical_tier", None)
            changes.append(
                ChangeItem(
                    date=after.observed_at,
                    axis=axis,
                    p_top_before=before.p_top,
                    p_top_after=after.p_top,
                    delta=delta,
                    tier_before=tier_before,
                    tier_after=tier_after,
                )
            )

    return TimelineResponse(server_id=server_id, days=days, changes=changes)


app = FastAPI()
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.score_change_timeline.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create an in‑memory SQLite DB that mimics the real schema
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    # Dependency override to use the in‑memory session
    def get_test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # Seed data
    with SessionLocal() as db:
        # Two servers
        db.add_all(
            [
                McpServerRegistry(server_id=1),
                McpServerRegistry(server_id=2),
            ]
        )
        # Axis scores for server 1 across three dates
        base_date = datetime.utcnow() - timedelta(days=2)
        db.add_all(
            [
                McpLlmAxisScore(
                    server_id=1,
                    axis="A",
                    p_top=0.10,
                    observed_at=base_date,
                ),
                McpLlmAxisScore(
                    server_id=1,
                    axis="A",
                    p_top=0.20,
                    observed_at=base_date + timedelta(days=1),
                ),
                McpLlmAxisScore(
                    server_id=1,
                    axis="A",
                    p_top=0.50,
                    observed_at=base_date + timedelta(days=2),
                ),
                # Additional axis for server 2 (should be ignored)
                McpLlmAxisScore(
                    server_id=2,
                    axis="B",
                    p_top=0.30,
                    observed_at=base_date,
                ),
            ]
        )
        db.commit()

    client = TestClient(app)

    resp = client.get("/api/scoring/timeline", params={"server_id": 1, "days": 3})
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["server_id"] == 1
    assert data["days"] == 3
    changes = data["changes"]
    # Three dates → two transitions
    assert len(changes) == 2, f"Expected 2 changes, got {len(changes)}"
    # Verify one known delta (0.50 - 0.10 = 0.40)
    deltas = {c["delta"] for c in changes}
    assert 0.40 in deltas, f"Expected delta 0.40 in {deltas}"

    print("PASS")