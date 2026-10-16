# services/staged/server_batch_verdict/logic.py
from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")


class BatchVerdictRequest(BaseModel):
    server_ids: List[str]


class OverallVerdict(BaseModel):
    label: str
    p_top: float
    escalated: bool


class AxisVerdict(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float


class ServerVerdict(BaseModel):
    server_name: str
    risk_tier: str
    overall: OverallVerdict
    axes: Dict[str, AxisVerdict]
    scored_at: datetime
    model_version: str
    decision_rule_version: str


class BatchVerdictResponse(BaseModel):
    servers: Dict[str, ServerVerdict]
    count: int


@router.post("/servers/verdicts/batch", response_model=BatchVerdictResponse)
def batch_verdict(
    request: BatchVerdictRequest,
    session=Depends(get_session),
) -> BatchVerdictResponse:
    ids = request.server_ids[:50]
    if not ids:
        return BatchVerdictResponse(servers={}, count=0)

    stmt = text(
        """
        SELECT
            a.server_id,
            a.axis_name,
            a.label,
            a.p_top,
            a.escalated,
            a.p_critical,
            a.p_danger,
            a.scored_at,
            a.model_version,
            a.decision_rule_version,
            s.name AS server_name,
            s.risk_tier
        FROM mcp_llm_axis_scores a
        JOIN mcp_server_registry s ON a.server_id = s.server_id
        WHERE a.server_id = ANY(:ids)
        """
    )
    rows = session.execute(stmt, {"ids": ids})
    result = rows.fetchall()

    servers: Dict[str, dict] = {}
    for row in result:
        sid = row.server_id
        if sid not in servers:
            servers[sid] = {
                "server_name": row.server_name,
                "risk_tier": row.risk_tier,
                "overall": None,
                "axes": {},
                "scored_at": row.scored_at,
                "model_version": row.model_version,
                "decision_rule_version": row.decision_rule_version,
            }

        if row.axis_name == "overall_risk":
            servers[sid]["overall"] = {
                "label": row.label,
                "p_top": row.p_top,
                "escalated": row.escalated,
            }
        else:
            servers[sid]["axes"][row.axis_name] = {
                "label": row.label,
                "p_top": row.p_top,
                "p_critical": row.p_critical,
                "p_danger": row.p_danger,
            }

    final: Dict[str, ServerVerdict] = {}
    for sid, data in servers.items():
        overall_data = data["overall"] or {"label": "", "p_top": 0.0, "escalated": False}
        final[sid] = ServerVerdict(
            server_name=data["server_name"],
            risk_tier=data["risk_tier"],
            overall=OverallVerdict(**overall_data),
            axes={k: AxisVerdict(**v) for k, v in data["axes"].items()},
            scored_at=data["scored_at"],
            model_version=data["model_version"],
            decision_rule_version=data["decision_rule_version"],
        )

    return BatchVerdictResponse(servers=final, count=len(final))


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from collections import namedtuple
    from fastapi.testclient import TestClient

    # Mock row matching the SELECT columns
    Row = namedtuple(
        "Row",
        [
            "server_id",
            "axis_name",
            "label",
            "p_top",
            "escalated",
            "p_critical",
            "p_danger",
            "scored_at",
            "model_version",
            "decision_rule_version",
            "server_name",
            "risk_tier",
        ],
    )

    # Sample data for three servers
    mock_rows = [
        # Server 1
        Row(
            "srv1",
            "overall_risk",
            "high",
            0.92,
            True,
            None,
            None,
            datetime.utcnow(),
            "v1.2",
            "dr1",
            "Server One",
            "critical",
        ),
        Row(
            "srv1",
            "auth_strength",
            "medium",
            0.65,
            False,
            0.30,
            0.10,
            datetime.utcnow(),
            "v1.2",
            "dr1",
            "Server One",
            "critical",
        ),
        # Server 2
        Row(
            "srv2",
            "overall_risk",
            "low",
            0.20,
            False,
            None,
            None,
            datetime.utcnow(),
            "v1.2",
            "dr1",
            "Server Two",
            "low",
        ),
        Row(
            "srv2",
            "auth_strength",
            "low",
            0.15,
            False,
            0.05,
            0.02,
            datetime.utcnow(),
            "v1.2",
            "dr1",
            "Server Two",
            "low",
        ),
        # Server 3
        Row(
            "srv3",
            "overall_risk",
            "medium",
            0.55,
            False,
            None,
            None,
            datetime.utcnow(),
            "v1.2",
            "dr1",
            "Server Three",
            "moderate",
        ),
        Row(
            "srv3",
            "auth_strength",
            "high",
            0.80,
            True,
            0.60,
            0.40,
            datetime.utcnow(),
            "v1.2",
            "dr1",
            "Server Three",
            "moderate",
        ),
    ]

    class MockResult:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class MockSession:
        def execute(self, stmt, params):
            # ignore stmt/params – return all mock rows for the requested ids
            filtered = [r for r in mock_rows if r.server_id in params["ids"]]
            return MockResult(filtered)

    def override_get_session():
        return MockSession()

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)

    client = TestClient(app)

    response = client.post(
        "/api/servers/verdicts/batch",
        json={"server_ids": ["srv1", "srv2", "srv3"]},
    )
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    assert data["count"] == 3, f"Count mismatch: {data['count']}"
    for sid in ["srv1", "srv2", "srv3"]:
        assert sid in data["servers"], f"{sid} missing"
        server = data["servers"][sid]
        assert "overall" in server, f"overall missing for {sid}"
        assert "axes" in server, f"axes missing for {sid}"
    print("PASS")