from __future__ import annotations

import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


class AdapterInfo(BaseModel):
    model_version: str
    adapter_sha256: str
    decision_rule_version: Optional[str]
    axes_covered: int
    server_count: int
    latest_scored_at: Optional[datetime]


class AdapterRegistryResponse(BaseModel):
    adapters: List[AdapterInfo]


def get_student_adapter_registry(session: Session) -> List[Dict[str, Any]]:
    """
    Build registry of active SFT student model adapters from axis scores.
    Keyed by model_version + adapter_sha256 with per-axis label coverage counts.
    """
    q = (
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.decision_rule_version,
            func.max(McpLlmAxisScore.scored_at).label("latest_scored_at"),
        )
        .group_by(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.decision_rule_version,
        )
        .order_by(McpLlmAxisScore.model_version, McpLlmAxisScore.adapter_sha256)
    )
    results = session.execute(q).all()

    adapter_keys = [(r.model_version, r.adapter_sha256) for r in results]

    axes_q = (
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            func.count(distinct(McpLlmAxisScore.axis_name)).label("axes_covered"),
        )
        .group_by(McpLlmAxisScore.model_version, McpLlmAxisScore.adapter_sha256)
    )
    axes_result = session.execute(axes_q).all()
    axes_covered = {(r.model_version, r.adapter_sha256): r.axes_covered for r in axes_result}

    server_q = (
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            func.count(distinct(McpLlmAxisScore.server_id)).label("server_count"),
        )
        .group_by(McpLlmAxisScore.model_version, McpLlmAxisScore.adapter_sha256)
    )
    server_result = session.execute(server_q).all()
    server_counts = {(r.model_version, r.adapter_sha256): r.server_count for r in server_result}

    return [
        {
            "model_version": r.model_version,
            "adapter_sha256": r.adapter_sha256,
            "decision_rule_version": r.decision_rule_version,
            "axes_covered": axes_covered.get((r.model_version, r.adapter_sha256), 0),
            "server_count": server_counts.get((r.model_version, r.adapter_sha256), 0),
            "latest_scored_at": r.latest_scored_at,
        }
        for r in results
    ]


def build_adapter_registry_response(session: Session) -> AdapterRegistryResponse:
    """Build the full adapter registry response."""
    adapters = get_student_adapter_registry(session)
    return AdapterRegistryResponse(
        adapters=[AdapterInfo(**a) for a in adapters]
    )


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base

    Base.metadata.create_all(bind=engine)

    TestingSession = sessionmaker(bind=engine)
    test_session = TestingSession()

    from datetime import timezone

    now = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

    rows = [
        McpLlmAxisScore(
            id=1,
            model_version="student-v1",
            adapter_sha256="sha111",
            axis_name="security",
            decision_rule_version="rule-v1",
            label="high",
            label_index=2,
            server_id="srv-1",
            scored_at=now,
            probs="[0.1,0.2,0.7]",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.7,
            escalated=False,
            escalated_to=None,
        ),
        McpLlmAxisScore(
            id=2,
            model_version="student-v1",
            adapter_sha256="sha111",
            axis_name="compliance",
            decision_rule_version="rule-v1",
            label="medium",
            label_index=1,
            server_id="srv-1",
            scored_at=now,
            probs="[0.2,0.6,0.2]",
            p_critical=0.2,
            p_danger=0.6,
            p_top=0.2,
            escalated=False,
            escalated_to=None,
        ),
        McpLlmAxisScore(
            id=3,
            model_version="student-v2",
            adapter_sha256="sha222",
            axis_name="security",
            decision_rule_version="rule-v2",
            label="low",
            label_index=0,
            server_id="srv-2",
            scored_at=now,
            probs="[0.7,0.2,0.1]",
            p_critical=0.7,
            p_danger=0.2,
            p_top=0.1,
            escalated=True,
            escalated_to="human-review",
        ),
    ]

    for row in rows:
        test_session.add(row)
    test_session.commit()

    app = FastAPI()

    def override_get_session():
        try:
            yield test_session
        finally:
            pass

    app.dependency_overrides[get_session] = override_get_session

    @app.get("/api/scoring/adapters")
    def get_adapters(session: Session = Depends(get_session)):
        return build_adapter_registry_response(session)

    with engine.begin() as conn:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        response = client.get("/api/scoring/adapters")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    adapters = data.get("adapters", [])
    assert len(adapters) == 2, f"Expected 2 adapters, got {len(adapters)}"

    adapter_map = {f"{a['model_version']}:{a['adapter_sha256']}": a for a in adapters}

    assert "student-v1:sha111" in adapter_map
    v1 = adapter_map["student-v1:sha111"]
    assert v1["axes_covered"] == 2, f"Expected axes_covered=2, got {v1['axes_covered']}"
    assert v1["server_count"] == 1, f"Expected server_count=1, got {v1['server_count']}"
    assert v1["decision_rule_version"] == "rule-v1"

    assert "student-v2:sha222" in adapter_map
    v2 = adapter_map["student-v2:sha222"]
    assert v2["axes_covered"] == 1, f"Expected axes_covered=1, got {v2['axes_covered']}"
    assert v2["server_count"] == 1, f"Expected server_count=1, got {v2['server_count']}"
    assert v2["decision_rule_version"] == "rule-v2"

    print("PASS")