"""Model version drift computation over the real application data layer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def _model_version_drift(db: Session, days: int) -> Dict[str, Any]:
    days = max(0, int(days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    grouped_rows = db.execute(
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.decision_rule_version,
            func.count(McpLlmAxisScore.id).label("rows"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("servers"),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.decision_rule_version,
        )
    ).all()

    rule_rows = db.execute(
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
            func.count(McpLlmAxisScore.id).label("rows"),
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
        )
    ).all()

    rule_counts: Dict[Any, Dict[Any, int]] = {}
    for model_version, decision_rule_version, rows in rule_rows:
        by_rule = rule_counts.setdefault(model_version, {})
        by_rule[decision_rule_version] = by_rule.get(decision_rule_version, 0) + int(rows)

    drift_shares: Dict[Any, float] = {}
    for model_version, by_rule in rule_counts.items():
        total_rows = sum(by_rule.values())
        common_rule = max(
            by_rule,
            key=lambda rule: (by_rule[rule], rule is not None, str(rule)),
        )
        drifted_rows = sum(
            count for rule, count in by_rule.items() if rule != common_rule
        )
        drift_shares[model_version] = drifted_rows / total_rows if total_rows else 0.0

    versions = [
        {
            "model_version": model_version,
            "adapter_sha256": adapter_sha256,
            "decision_rule_version": decision_rule_version,
            "rows": int(rows),
            "servers": int(servers),
            "first_scored_at": first_scored_at,
            "last_scored_at": last_scored_at,
            "drift_share": drift_shares.get(model_version, 0.0),
        }
        for (
            model_version,
            adapter_sha256,
            decision_rule_version,
            rows,
            servers,
            first_scored_at,
            last_scored_at,
        ) in grouped_rows
    ]
    versions.sort(
        key=lambda row: (
            str(row["model_version"]),
            str(row["adapter_sha256"]),
            str(row["decision_rule_version"]),
        )
    )

    totals_row = db.execute(
        select(
            func.count(McpLlmAxisScore.id),
            func.count(func.distinct(McpLlmAxisScore.server_id)),
            func.count(func.distinct(McpLlmAxisScore.model_version)),
        ).where(McpLlmAxisScore.scored_at >= cutoff)
    ).one()

    return {
        "versions": versions,
        "totals": {
            "rows": int(totals_row[0]),
            "servers": int(totals_row[1]),
            "versions": int(totals_row[2]),
        },
    }


def model_version_drift(db: Session, days: int = 30) -> Dict[str, Any]:
    """Return model-version drift statistics for the requested lookback window."""
    result = _model_version_drift(db, days)
    return {"days": max(0, int(days)), **result}


def get_model_version_drift(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility name for callers using a get-style service function."""
    return model_version_drift(db, days)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as db:
        db.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    model_version="model-v1",
                    adapter_sha256="adapter-sha",
                    decision_rule_version="rule-v1",
                    axis_name="security",
                    server_id=101,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=2,
                    model_version="model-v1",
                    adapter_sha256="adapter-sha",
                    decision_rule_version="rule-v2",
                    axis_name="security",
                    server_id=102,
                    scored_at=now - timedelta(days=1),
                ),
                McpLlmAxisScore(
                    id=3,
                    model_version="model-v2",
                    adapter_sha256="adapter-sha",
                    decision_rule_version="rule-v1",
                    axis_name="security",
                    server_id=101,
                    scored_at=now - timedelta(days=1),
                ),
            ]
        )
        db.commit()

    that_app = FastAPI()

    @that_app.get("/api/scoring/model-version-drift")
    def drift_endpoint(days: int = 30, db: Session = Depends(get_session)):
        return model_version_drift(db, days)

    that_app.dependency_overrides[get_session] = lambda: TestSession()
    with TestClient(that_app) as client:
        response = client.get("/api/scoring/model-version-drift", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["versions"]) == 3
        assert any(row["rows"] == 1 for row in payload["versions"])
        assert all(0.0 <= row["drift_share"] <= 1.0 for row in payload["versions"])
        assert payload["totals"]["rows"] == 3
        assert payload["totals"]["servers"] == 2
        assert payload["totals"]["versions"] == 2

    print("PASS")