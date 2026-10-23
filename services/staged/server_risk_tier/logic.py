"""
services/staged/server_risk_tier/logic.py

Logic for the ``/api/servers/{server_id}/risk-tier`` endpoint.

The implementation mirrors the exemplar service logic and operates directly on the
real application models (no stubs).  It computes a weighted composite score from
the ``McpLlmAxisScore`` table joined with ``McpServerRegistry`` and derives
a risk tier according to the specification.

The module also contains a self‑test that runs against an in‑memory SQLite
database and prints ``PASS`` when the contract is satisfied.
"""

from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Real application imports – these must be used verbatim.
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


# --------------------------------------------------------------------------- #
# Risk tier definition
# --------------------------------------------------------------------------- #
_RISK_TIERS: List[Dict[str, object]] = [
    {"name": "TRUSTED_GENERAL", "min": 75.0},
    {"name": "TRUSTED_RESEARCH", "min": 60.0},
    {"name": "ENTERPRISE_CONTROLLED", "min": 45.0},
    {"name": "CAUTION_LIMITED", "min": 30.0},
    {"name": "HIGH_RISK_ISOLATED", "min": 15.0},
    {"name": "KNOWN_THREAT", "min": -float("inf")},
    # INSUFFICIENT is handled separately when too many axes are missing.
]

# The total number of distinct axes that the scoring model expects.
# This constant is used only for the INSUFFICIENT check.
_EXPECTED_AXIS_COUNT = 10


def _determine_risk_tier(composite: float, axis_count: int) -> str:
    """
    Determine the risk tier from a composite score and the number of axes
    present for the server.

    If the server is missing five or more axes, the tier is ``INSUFFICIENT``.
    Otherwise the tier is chosen from the ordered ``_RISK_TIERS`` list.
    """
    missing_axes = _EXPECTED_AXIS_COUNT - axis_count
    if missing_axes >= 5:
        return "INSUFFICIENT"

    for tier in _RISK_TIERS:
        if composite > tier["min"]:
            return tier["name"]
    # Fallback – should never be reached because the last tier matches all.
    return "KNOWN_THREAT"


@router.get(
    "/api/servers/{server_id}/risk-tier",
    response_model=dict,
    summary="Get the risk tier for a server",
)
def get_server_risk_tier(
    server_id: int,
    session: Session = Depends(get_session),
) -> Dict[str, object]:
    """
    Compute the weighted composite score for *server_id* and return the
    derived risk tier together with auxiliary information.
    """
    # ------------------------------------------------------------------- #
    # Retrieve the server registry entry – needed for ``criteria_version``.
    # ------------------------------------------------------------------- #
    server_row = session.get(McpServerRegistry, server_id)
    if server_row is None:
        raise HTTPException(status_code=404, detail="Server not found")

    # ------------------------------------------------------------------- #
    # Aggregate axis scores for the server.
    # ------------------------------------------------------------------- #
    stmt = (
        select(
            func.sum(McpLlmAxisScore.score * McpLlmAxisScore.weight).label(
                "weighted_sum"
            ),
            func.sum(McpLlmAxisScore.weight).label("total_weight"),
            func.count(McpLlmAxisScore.axis_name).label("axis_count"),
        )
        .where(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.server_id)
    )
    result = session.execute(stmt).first()

    if result is None:
        # No axis scores at all – treat as insufficient data.
        composite_score = 0.0
        axis_count = 0
    else:
        weighted_sum = result.weighted_sum or 0.0
        total_weight = result.total_weight or 0.0
        axis_count = result.axis_count or 0

        if total_weight == 0:
            composite_score = 0.0
        else:
            composite_score = weighted_sum / total_weight

    # ------------------------------------------------------------------- #
    # Determine the risk tier.
    # ------------------------------------------------------------------- #
    risk_tier = _determine_risk_tier(composite_score, axis_count)

    # The ``verdict`` field mirrors the tier name for now – this can be
    # adjusted later without breaking the contract.
    response = {
        "server_id": server_id,
        "composite_score": float(composite_score),
        "risk_tier": risk_tier,
        "verdict": risk_tier,
        "axis_count": axis_count,
        "criteria_version": getattr(server_row, "criteria_version", None),
    }
    return response


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    # The self‑test builds an in‑memory SQLite database, seeds it with a small
    # data set, and exercises the endpoint via a TestClient.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite engine and bind the real model metadata.
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    # Import the declarative base from the app models – it is named ``Base``.
    from app.models import Base  # type: ignore

    Base.metadata.create_all(engine)

    TestSession = sessionmaker(bind=engine)

    # ------------------------------------------------------------------- #
    # Helper to insert rows using the test session.
    # ------------------------------------------------------------------- #
    def seed_data() -> None:
        sess = TestSession()
        # Servers
        servers = [
            McpServerRegistry(id=1, criteria_version="v1"),
            McpServerRegistry(id=2, criteria_version="v1"),
            McpServerRegistry(id=3, criteria_version="v1"),
        ]
        sess.add_all(servers)

        # Axis scores – weight is set to 1 for simplicity.
        # Server 1: high scores → composite > 75 → TRUSTED_GENERAL
        for axis in range(_EXPECTED_AXIS_COUNT):
            sess.add(
                McpLlmAxisScore(
                    server_id=1,
                    axis_name=f"axis_{axis}",
                    score=90.0,
                    weight=1.0,
                )
            )
        # Server 2: moderate scores → composite ~ 65 → TRUSTED_RESEARCH
        for axis in range(_EXPECTED_AXIS_COUNT):
            sess.add(
                McpLlmAxisScore(
                    server_id=2,
                    axis_name=f"axis_{axis}",
                    score=65.0,
                    weight=1.0,
                )
            )
        # Server 3: only two axes → insufficient data
        sess.add(
            McpLlmAxisScore(
                server_id=3,
                axis_name="axis_0",
                score=50.0,
                weight=1.0,
            )
        )
        sess.add(
            McpLlmAxisScore(
                server_id=3,
                axis_name="axis_1",
                score=55.0,
                weight=1.0,
            )
        )
        sess.commit()
        sess.close()

    seed_data()

    # ------------------------------------------------------------------- #
    # Build a FastAPI app that uses the test session instead of the real DB.
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    def get_test_session() -> Session:  # pragma: no cover
        """Dependency override that yields a session bound to the in‑memory DB."""
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute the contract checks.
    # ------------------------------------------------------------------- #
    valid_tiers = {
        "TRUSTED_GENERAL",
        "TRUSTED_RESEARCH",
        "ENTERPRISE_CONTROLLED",
        "CAUTION_LIMITED",
        "HIGH_RISK_ISOLATED",
        "KNOWN_THREAT",
        "INSUFFICIENT",
    }

    for srv_id, expected_tier in [(1, "TRUSTED_GENERAL"), (2, "TRUSTED_RESEARCH"), (3, "INSUFFICIENT")]:
        resp = client.get(f"/api/servers/{srv_id}/risk-tier")
        assert resp.status_code == 200, f"Unexpected status {resp.status_code} for server {srv_id}"
        data = resp.json()
        assert isinstance(data["composite_score"], float), "composite_score not a float"
        assert data["risk_tier"] in valid_tiers, f"Invalid tier {data['risk_tier']}"
        assert data["risk_tier"] == expected_tier, f"Expected {expected_tier}, got {data['risk_tier']}"

    print("PASS")