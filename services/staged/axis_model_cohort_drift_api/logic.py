"""logic.py -- axis model cohort drift reporting over the real application data.

Pure computation over the application's SQLAlchemy session. The module imports
only the real data layer and model; the local database in __main__ is used
solely for the self-test.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def _utc_datetime(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime for values from either backend."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def cohort_report(conn: Session, days: int = 30) -> Dict[str, Any]:
    """Report live model and decision-rule cohorts from recent axis scores.

    A cohort is the combination of decision-rule version, model version, and
    adapter checksum. Scores older than the requested lookback window are
    excluded from both the totals and cohort groupings.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    rows = conn.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.scored_at,
        ).where(McpLlmAxisScore.scored_at >= cutoff)
    ).all()

    grouped: Dict[tuple[Any, Any, Any], Dict[str, Any]] = {}
    axis_cohorts: Dict[str, set[tuple[Any, Any, Any]]] = {}
    axis_versions: Dict[str, set[Any]] = {}

    for server_id, axis_name, decision_rule_version, model_version, adapter_sha256, scored_at in rows:
        if scored_at is None:
            continue

        key = (decision_rule_version, model_version, adapter_sha256)
        timestamp = _utc_datetime(scored_at)

        if key not in grouped:
            grouped[key] = {
                "decision_rule_version": decision_rule_version,
                "model_version": model_version,
                "adapter_sha256": adapter_sha256,
                "rows": 0,
                "servers": set(),
                "first_scored_at": timestamp,
                "last_scored_at": timestamp,
            }

        cohort = grouped[key]
        cohort["rows"] += 1
        cohort["servers"].add(server_id)
        if timestamp < cohort["first_scored_at"]:
            cohort["first_scored_at"] = timestamp
        if timestamp > cohort["last_scored_at"]:
            cohort["last_scored_at"] = timestamp

        axis_key = axis_name or "unknown"
        axis_cohorts.setdefault(axis_key, set()).add(key)
        axis_versions.setdefault(axis_key, set()).add(model_version)

    cohorts: List[Dict[str, Any]] = []
    stale_cohorts: List[Dict[str, Any]] = []

    for cohort in grouped.values():
        first_scored_at = cohort["first_scored_at"].isoformat()
        last_scored_at = cohort["last_scored_at"].isoformat()
        age_days = max(0, int((now - cohort["last_scored_at"]).total_seconds() // 86400))

        cohorts.append(
            {
                "decision_rule_version": cohort["decision_rule_version"],
                "model_version": cohort["model_version"],
                "adapter_sha256": cohort["adapter_sha256"],
                "rows": cohort["rows"],
                "distinct_servers": len(cohort["servers"]),
                "first_scored_at": first_scored_at,
                "last_scored_at": last_scored_at,
            }
        )

        if days > 0 and age_days >= days:
            stale_cohorts.append(
                {
                    "decision_rule_version": cohort["decision_rule_version"],
                    "model_version": cohort["model_version"],
                    "rows": cohort["rows"],
                    "age_days": age_days,
                }
            )

    per_axis_cohorts = [
        {
            "axis_name": axis_name,
            "cohort_count": len(axis_cohorts[axis_name]),
            "distinct_versions": len(axis_versions[axis_name]),
        }
        for axis_name in sorted(axis_cohorts)
    ]

    return {
        "days": days,
        "total_rows": len(rows),
        "cohorts": cohorts,
        "stale_cohorts": stale_cohorts,
        "per_axis_cohorts": per_axis_cohorts,
    }


if __name__ == "__main__":
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
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    test_session = session_factory()

    now = datetime.now(timezone.utc)
    test_session.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="server-a",
                axis_name="security",
                decision_rule_version="rule-1",
                model_version="model-a",
                adapter_sha256="sha-a",
                scored_at=now - timedelta(days=2),
            ),
            McpLlmAxisScore(
                id=2,
                server_id="server-b",
                axis_name="security",
                decision_rule_version="rule-1",
                model_version="model-a",
                adapter_sha256="sha-a",
                scored_at=now - timedelta(days=1),
            ),
            McpLlmAxisScore(
                id=3,
                server_id="server-a",
                axis_name="security",
                decision_rule_version="rule-1",
                model_version="model-b",
                adapter_sha256="sha-b",
                scored_at=now - timedelta(days=3),
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/axis/model-cohorts")
    def model_cohorts(
        days: int = 30,
        conn: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return cohort_report(conn, days)

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/axis/model-cohorts?days=30")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["cohorts"]) == 2
    assert payload["total_rows"] == 3
    assert sorted(c["distinct_servers"] for c in payload["cohorts"]) == [1, 2]
    assert payload["per_axis_cohorts"]

    print("PASS")