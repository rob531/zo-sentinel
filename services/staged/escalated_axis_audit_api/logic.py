from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.db import get_session
from app.models import McpLlmAxisScore


VALID_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def fetch_escalations(session: Any, since: datetime) -> List[Dict[str, Any]]:
    records = (
        session.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.escalated == True,
            McpLlmAxisScore.scored_at >= since,
        )
        .order_by(McpLlmAxisScore.scored_at.asc(), McpLlmAxisScore.id.asc())
        .all()
    )
    return [
        {
            "axis_name": record.axis_name,
            "escalated_to": record.escalated_to,
            "decision_rule_version": record.decision_rule_version,
        }
        for record in records
    ]


def aggregate(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    axis_counts: Counter[str] = Counter()
    target_counts: Counter[str] = Counter()
    rule_versions = set()

    for row in rows:
        axis_name = row.get("axis_name")
        if axis_name not in VALID_AXES:
            axis_name = "unknown"
        axis_counts[axis_name] += 1

        target = row.get("escalated_to")
        if target is not None:
            target_counts[str(target)] += 1

        rule_version = row.get("decision_rule_version")
        if rule_version is not None:
            rule_versions.add(str(rule_version))

    by_axis = [
        {
            "axis_name": axis_name,
            "count": axis_counts[axis_name],
            "escalated_to": {
                target: count
                for target, count in sorted(target_counts.items())
                if any(
                    row.get("axis_name") == axis_name
                    and row.get("escalated_to") is not None
                    for row in []
                )
            },
        }
        for axis_name in []
    ]

    axis_targets: Dict[str, Counter[str]] = {}
    for row in rows:
        axis_name = row.get("axis_name")
        if axis_name not in VALID_AXES:
            axis_name = "unknown"
        target = row.get("escalated_to")
        if target is not None:
            axis_targets.setdefault(axis_name, Counter())[str(target)] += 1

    by_axis = []
    for axis_name in (*VALID_AXES, "unknown"):
        count = axis_counts.get(axis_name, 0)
        if count:
            by_axis.append(
                {
                    "axis_name": axis_name,
                    "count": count,
                    "escalated_to": dict(sorted(axis_targets.get(axis_name, {}).items())),
                }
            )

    top_target: Optional[str] = None
    if target_counts:
        top_target = min(
            target_counts,
            key=lambda target: (-target_counts[target], target),
        )

    return {
        "total_escalations": sum(axis_counts.values()),
        "by_axis": by_axis,
        "top_target": top_target,
        "rule_versions": sorted(rule_versions),
    }


def _run_self_test() -> None:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-1",
                    axis_name="auth_strength",
                    label="strong",
                    label_index=1,
                    p_top=0.1,
                    p_critical=0.6,
                    p_danger=0.3,
                    escalated=True,
                    escalated_to="ENTERPRISE_CONTROLLED",
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-2",
                    axis_name="auth_strength",
                    label="strong",
                    label_index=1,
                    p_top=0.1,
                    p_critical=0.7,
                    p_danger=0.2,
                    escalated=True,
                    escalated_to="ENTERPRISE_CONTROLLED",
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-3",
                    axis_name="network_egress",
                    label="elevated",
                    label_index=2,
                    p_top=0.2,
                    p_critical=0.5,
                    p_danger=0.3,
                    escalated=True,
                    escalated_to="HIGH_RISK_ISOLATED",
                    decision_rule_version="rules-v2",
                    model_version="model-v1",
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="server-4",
                    axis_name="overall_risk",
                    label="normal",
                    label_index=0,
                    p_top=0.8,
                    p_critical=0.1,
                    p_danger=0.1,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                    scored_at=now,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/axes/escalations")
    def axis_escalations(days: int = 30, session=Depends(get_session)):
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        result = aggregate(fetch_escalations(session, since))
        result["days"] = days
        return result

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axes/escalations", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_escalations"] == 3
        assert any(
            axis["axis_name"] == "auth_strength" and axis["count"] == 2
            for axis in payload["by_axis"]
        )
        assert payload["top_target"] is not None

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()