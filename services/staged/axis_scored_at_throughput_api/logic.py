from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def compute_scoring_throughput(session: Session, days: int = 7) -> dict[str, Any]:
    """Compute throughput and scoring-provenance statistics for recent scores."""
    if days < 1:
        raise ValueError("days must be at least 1")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    statement = select(
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.decision_rule_version,
        McpLlmAxisScore.model_version,
    ).where(McpLlmAxisScore.scored_at >= cutoff)

    rows = session.execute(statement).all()

    daily_rows: Counter[str] = Counter()
    daily_servers: defaultdict[str, set[str]] = defaultdict(set)
    axis_counts: Counter[str] = Counter()
    rule_versions: Counter[str] = Counter()
    model_versions: Counter[str] = Counter()

    for scored_at, server_id, axis_name, rule_version, model_version in rows:
        if scored_at is None:
            continue

        if isinstance(scored_at, datetime):
            date_key = scored_at.date().isoformat()
        else:
            date_key = str(scored_at)[:10]

        daily_rows[date_key] += 1
        if server_id is not None:
            daily_servers[date_key].add(str(server_id))

        if axis_name is not None:
            axis_counts[str(axis_name)] += 1
        if rule_version is not None:
            rule_versions[str(rule_version)] += 1
        if model_version is not None:
            model_versions[str(model_version)] += 1

    daily = [
        {
            "date": date_key,
            "rows": daily_rows[date_key],
            "servers": len(daily_servers[date_key]),
        }
        for date_key in sorted(daily_rows)
    ]

    return {
        "days": days,
        "daily": daily,
        "by_axis": dict(sorted(axis_counts.items())),
        "rule_versions": dict(sorted(rule_versions.items())),
        "model_versions": dict(sorted(model_versions.items())),
    }


def get_scoring_throughput(
    session: Session = Depends(get_session), days: int = 7
) -> dict[str, Any]:
    return compute_scoring_throughput(session=session, days=days)


# Compatibility aliases for staged router conventions.
get_axis_scored_at_throughput = get_scoring_throughput
get_scored_at_throughput = get_scoring_throughput


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(test_engine)
    test_session_factory = __import__("sqlalchemy.orm", fromlist=["sessionmaker"]).sessionmaker(
        bind=test_engine
    )

    with test_session_factory() as session:
        now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        day_one = now - timedelta(days=1)
        day_two = now

        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-a",
                    axis_name="security",
                    label="high",
                    label_index=1,
                    probs={},
                    p_top=0.8,
                    p_critical=0.1,
                    p_danger=0.1,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rule-v1",
                    model_version="model-v1",
                    adapter_sha256="sha-a",
                    scored_at=day_one,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-b",
                    axis_name="quality",
                    label="medium",
                    label_index=2,
                    probs={},
                    p_top=0.7,
                    p_critical=0.05,
                    p_danger=0.15,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rule-v2",
                    model_version="model-v1",
                    adapter_sha256="sha-b",
                    scored_at=day_two,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-a",
                    axis_name="security",
                    label="low",
                    label_index=3,
                    probs={},
                    p_top=0.6,
                    p_critical=0.02,
                    p_danger=0.08,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rule-v1",
                    model_version="model-v2",
                    adapter_sha256="sha-a",
                    scored_at=day_two,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/scoring/throughput")
    def scoring_throughput(
        session: Session = Depends(get_session), days: int = 7
    ) -> dict[str, Any]:
        return compute_scoring_throughput(session=session, days=days)

    response = TestClient(that_app).get("/api/scoring/throughput", params={"days": 7})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["daily"]) == 2
    assert sum(item["rows"] for item in payload["daily"]) == 3
    assert set(payload["by_axis"]) == {"security", "quality"}
    assert len(payload["rule_versions"]) == 2
    print("PASS")