# services/staged/axis_p_critical_decile_api/logic.py
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field, conint

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api")


class Bucket(BaseModel):
    bucket_index: int = Field(..., description="Zero‑based bucket index")
    lower: float = Field(..., description="Inclusive lower bound of the bucket")
    upper: float = Field(..., description="Exclusive upper bound of the bucket")
    count: int = Field(..., description="Number of rows in the bucket")


class DecileResponse(BaseModel):
    axis: str
    deciles: int
    total_count: int
    buckets: List[Bucket]


def _compute_histogram(
    values: List[float], deciles: int
) -> List[Bucket]:
    """Return bucket objects for the supplied probability values."""
    bucket_counts = [0] * deciles
    for v in values:
        # Clamp to [0,1] just in case
        v = max(0.0, min(1.0, v))
        idx = int(v * deciles)
        if idx == deciles:  # value == 1.0
            idx = deciles - 1
        bucket_counts[idx] += 1

    buckets: List[Bucket] = []
    step = 1.0 / deciles
    for i, cnt in enumerate(bucket_counts):
        lower = i * step
        upper = (i + 1) * step
        buckets.append(
            Bucket(
                bucket_index=i,
                lower=lower,
                upper=upper,
                count=cnt,
            )
        )
    return buckets


@router.get(
    "/axis/p-critical/decile",
    response_model=List[DecileResponse],
    summary="Histogram of p_critical values per axis",
)
def get_axis_p_critical_decile(
    axis: Optional[str] = Query(
        None,
        description="Axis name to filter on; if omitted, all axes are returned",
    ),
    deciles: conint(ge=2, le=20) = Query(
        10,
        description="Number of equal‑width buckets (clamped 2‑20)",
    ),
    session: Session = Depends(get_session),
):
    """
    Return a histogram of `p_critical` values split into *deciles* (or any
    number of equal‑width buckets) for the requested axis or for all axes.
    """
    # Resolve which axes to process
    if axis:
        axes = [axis]
    else:
        axes_query = session.query(McpLlmAxisScore.axis_name).distinct()
        axes = [row[0] for row in axes_query.all()]

    if not axes:
        raise HTTPException(status_code=404, detail="No axis data found")

    responses: List[DecileResponse] = []

    for ax in axes:
        # Pull all p_critical values for this axis
        values_query = (
            session.query(McpLlmAxisScore.p_critical)
            .filter(McpLlmAxisScore.axis_name == ax)
            .order_by(McpLlmAxisScore.id)
        )
        values = [row[0] for row in values_query.all()]

        total = len(values)
        buckets = _compute_histogram(values, deciles)

        responses.append(
            DecileResponse(
                axis=ax,
                deciles=deciles,
                total_count=total,
                buckets=buckets,
            )
        )

    return responses


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import Base  # Base metadata for table creation

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mimics the real schema
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # ------------------------------------------------------------------- #
    # Dependency override to supply the test session
    # ------------------------------------------------------------------- #
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed the DB with deterministic data
    # ------------------------------------------------------------------- #
    seed_session = TestSession()
    rows = [
        McpLlmAxisScore(
            axis_name="axis1",
            p_critical=0.05,
            adapter_sha256="a" * 64,
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=1,
            label="lbl",
            label_index=0,
            model_version="m1",
            p_danger=0.0,
            p_top=0.0,
            probs="[]",
            scored_at=func.now(),
            server_id=1,
        ),
        McpLlmAxisScore(
            axis_name="axis1",
            p_critical=0.15,
            adapter_sha256="b" * 64,
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=2,
            label="lbl",
            label_index=0,
            model_version="m1",
            p_danger=0.0,
            p_top=0.0,
            probs="[]",
            scored_at=func.now(),
            server_id=2,
        ),
        McpLlmAxisScore(
            axis_name="axis2",
            p_critical=0.55,
            adapter_sha256="c" * 64,
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=3,
            label="lbl",
            label_index=0,
            model_version="m1",
            p_danger=0.0,
            p_top=0.0,
            probs="[]",
            scored_at=func.now(),
            server_id=3,
        ),
        McpLlmAxisScore(
            axis_name="axis2",
            p_critical=0.95,
            adapter_sha256="d" * 64,
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=4,
            label="lbl",
            label_index=0,
            model_version="m1",
            p_danger=0.0,
            p_top=0.0,
            probs="[]",
            scored_at=func.now(),
            server_id=4,
        ),
    ]
    seed_session.add_all(rows)
    seed_session.commit()
    seed_session.close()

    # ------------------------------------------------------------------- #
    # Assemble FastAPI app with the router and override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform a request for a single axis and validate the contract
    # ------------------------------------------------------------------- #
    resp = client.get("/api/axis/p-critical/decile", params={"axis": "axis1", "deciles": 10})
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert isinstance(data, list) and len(data) == 1, "Expected a single‑element list"
    result = data[0]
    assert result["axis"] == "axis1"
    assert result["deciles"] == 10
    assert len(result["buckets"]) == 10, "Bucket count mismatch"
    total = sum(b["count"] for b in result["buckets"])
    assert total == 2, f"Expected 2 rows for axis1, got {total}"

    # ------------------------------------------------------------------- #
    # Request without specifying axis (all axes) and verify totals
    # ------------------------------------------------------------------- #
    resp_all = client.get("/api/axis/p-critical/decile", params={"deciles": 5})
    assert resp_all.status_code == 200
    all_data = resp_all.json()
    assert isinstance(all_data, list) and len(all_data) == 2
    totals = sum(item["total_count"] for item in all_data)
    assert totals == 4, f"Expected 4 rows total, got {totals}"

    print("PASS")