# deps: fastapi, sqlalchemy, pydantic

"""FastAPI router for axis escalation census.

Returns counts and per-server details of escalated / high-risk (p_top >= 0.7)
servers grouped by axis, from mcp_llm_axis_scores.
Auth is public per directive config.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["axis_escalation_census_api"])


class ServerEscalationInfo(BaseModel):
    server_id: str
    p_top: float
    escalated: bool

    class Config:
        from_attributes = True


class AxisEscalationCensus(BaseModel):
    axis_name: str
    escalated_count: int
    high_risk_count: int
    servers: list[ServerEscalationInfo]


class EscalationCensusResponse(BaseModel):
    generated_at: str
    axes: list[AxisEscalationCensus]


# Valid axis names
VALID_AXES = frozenset({
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
})


@router.get("/axes/escalation-census", response_model=EscalationCensusResponse)
def get_escalation_census(
    axis_name: str | None = Query(None, description="Filter to a specific axis"),
    db: Session = Depends(get_session),
):
    """Return escalated and high-risk servers grouped by axis.

    Escalated = explicitly flagged escalated=True.
    High-risk = p_top >= 0.7 (escalated or not).
    """
    query = db.query(McpLlmAxisScore).filter(
        or_(McpLlmAxisScore.escalated == True, McpLlmAxisScore.p_top >= 0.7)
    )
    if axis_name:
        if axis_name not in VALID_AXES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid axis_name: {axis_name}. Must be one of: {sorted(VALID_AXES)}",
            )
        query = query.filter(McpLlmAxisScore.axis_name == axis_name)

    rows = query.order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at.desc()).all()

    axes_map: dict[str, dict] = {}
    for row in rows:
        aname = row.axis_name
        if aname not in axes_map:
            axes_map[aname] = {
                "servers": [],
                "escalated_count": 0,
                "high_risk_count": 0,
            }
        axes_map[aname]["servers"].append({
            "server_id": row.server_id,
            "p_top": row.p_top,
            "escalated": row.escalated,
        })
        if row.escalated:
            axes_map[aname]["escalated_count"] += 1
        if row.p_top >= 0.7:
            axes_map[aname]["high_risk_count"] += 1

    axes = [
        AxisEscalationCensus(
            axis_name=aname,
            escalated_count=data["escalated_count"],
            high_risk_count=data["high_risk_count"],
            servers=[ServerEscalationInfo(**s) for s in data["servers"]],
        )
        for aname, data in sorted(axes_map.items())
    ]

    return EscalationCensusResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        axes=axes,
    )


if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # Isolated test app -- no app.main dependency
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    _test_db = TestSession()

    # Seed data:
    # axis_a: escalated + high-risk, high-risk-only
    # axis_b: only high-risk
    # axis_c: escalated (p_top < 0.7, not high-risk)
    # axis_d: low p_top, no escalation -- should NOT appear
    _test_db.add(McpLlmAxisScore(
        axis_name="axis_a", server_id="srv001", p_top=0.85, escalated=True,
        scored_at=datetime.now(), adapter_sha256="sha", model_version="v1",
        decision_rule_version="r1",
    ))
    _test_db.add(McpLlmAxisScore(
        axis_name="axis_a", server_id="srv002", p_top=0.72, escalated=False,
        scored_at=datetime.now(), adapter_sha256="sha", model_version="v1",
        decision_rule_version="r1",
    ))
    _test_db.add(McpLlmAxisScore(
        axis_name="axis_b", server_id="srv003", p_top=0.90, escalated=False,
        scored_at=datetime.now(), adapter_sha256="sha", model_version="v1",
        decision_rule_version="r1",
    ))
    _test_db.add(McpLlmAxisScore(
        axis_name="axis_c", server_id="srv004", p_top=0.60, escalated=True,
        scored_at=datetime.now(), adapter_sha256="sha", model_version="v1",
        decision_rule_version="r1",
    ))
    _test_db.add(McpLlmAxisScore(
        axis_name="axis_d", server_id="srv005", p_top=0.30, escalated=False,
        scored_at=datetime.now(), adapter_sha256="sha", model_version="v1",
        decision_rule_version="r1",
    ))
    _test_db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def _override():
        yield _test_db

    test_app.dependency_overrides[get_session] = _override
    client = TestClient(test_app)

    # --- Happy path ---
    resp = client.get("/api/axes/escalation-census")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if "generated_at" not in data:
        print(f"FAIL: missing generated_at: {data}")
        sys.exit(1)
    if "axes" not in data:
        print(f"FAIL: missing axes: {data}")
        sys.exit(1)

    axes_dict = {ax["axis_name"]: ax for ax in data["axes"]}

    # axis_a: 1 escalated, 2 high-risk
    if axes_dict["axis_a"]["escalated_count"] != 1:
        print(f"FAIL: axis_a escalated_count={axes_dict['axis_a']['escalated_count']}, expected 1")
        sys.exit(1)
    if axes_dict["axis_a"]["high_risk_count"] != 2:
        print(f"FAIL: axis_a high_risk_count={axes_dict['axis_a']['high_risk_count']}, expected 2")
        sys.exit(1)

    # axis_b: 0 escalated, 1 high-risk
    if axes_dict["axis_b"]["escalated_count"] != 0:
        print(f"FAIL: axis_b escalated_count={axes_dict['axis_b']['escalated_count']}, expected 0")
        sys.exit(1)
    if axes_dict["axis_b"]["high_risk_count"] != 1:
        print(f"FAIL: axis_b high_risk_count={axes_dict['axis_b']['high_risk_count']}, expected 1")
        sys.exit(1)

    # axis_c: 1 escalated, 0 high-risk (p_top=0.60 < 0.7)
    if axes_dict["axis_c"]["escalated_count"] != 1:
        print(f"FAIL: axis_c escalated_count={axes_dict['axis_c']['escalated_count']}, expected 1")
        sys.exit(1)
    if axes_dict["axis_c"]["high_risk_count"] != 0:
        print(f"FAIL: axis_c high_risk_count={axes_dict['axis_c']['high_risk_count']}, expected 0")
        sys.exit(1)

    # axis_d must NOT appear
    if "axis_d" in axes_dict:
        print(f"FAIL: axis_d should not appear in census")
        sys.exit(1)

    # --- Axis filter happy path ---
    resp2 = client.get("/api/axes/escalation-census?axis_name=axis_a")
    if resp2.status_code != 200:
        print(f"FAIL: axis filter returned {resp2.status_code}: {resp2.text}")
        sys.exit(1)
    if len(resp2.json()["axes"]) != 1:
        print(f"FAIL: axis filter should return exactly 1 axis, got {len(resp2.json()['axes'])}")
        sys.exit(1)

    # --- Validation failure ---
    resp3 = client.get("/api/axes/escalation-census?axis_name=not_a_real_axis")
    if resp3.status_code != 400:
        print(f"FAIL: expected 400 for invalid axis, got {resp3.status_code}")
        sys.exit(1)

    # --- Auth failure: clear override so DB access fails ---
    test_app.dependency_overrides.clear()
    resp4 = client.get("/api/axes/escalation-census")
    if resp4.status_code == 200:
        print(f"FAIL: expected non-200 without session override, got {resp4.status_code}")
        sys.exit(1)

    _test_db.close()
    print("PASS")
