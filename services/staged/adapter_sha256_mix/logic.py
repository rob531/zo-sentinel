"""Logic for the adapter SHA-256 scoring mix service.

Reads the real application score table through the application's SQLAlchemy
session and returns adapter usage statistics for the API layer.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def _iso_timestamp(value: object) -> Optional[str]:
    """Return a database timestamp as an ISO-8601 string."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_adapter_mix(db: Session) -> Dict[str, object]:
    """Summarize scoring rows by the adapter that produced them."""
    summary_rows = db.execute(
        select(
            McpLlmAxisScore.adapter_sha256,
            func.count(McpLlmAxisScore.id),
            func.min(McpLlmAxisScore.scored_at),
            func.max(McpLlmAxisScore.scored_at),
        )
        .where(McpLlmAxisScore.adapter_sha256.is_not(None))
        .group_by(McpLlmAxisScore.adapter_sha256)
    ).all()

    model_rows = db.execute(
        select(
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.model_version,
        )
        .where(
            McpLlmAxisScore.adapter_sha256.is_not(None),
            McpLlmAxisScore.model_version.is_not(None),
        )
        .distinct()
    ).all()

    decision_rows = db.execute(
        select(
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.decision_rule_version,
        )
        .where(
            McpLlmAxisScore.adapter_sha256.is_not(None),
            McpLlmAxisScore.decision_rule_version.is_not(None),
        )
        .distinct()
    ).all()

    model_versions: Dict[str, set[str]] = {}
    for adapter, model_version in model_rows:
        model_versions.setdefault(adapter, set()).add(model_version)

    decision_rule_versions: Dict[str, set[str]] = {}
    for adapter, decision_rule_version in decision_rows:
        decision_rule_versions.setdefault(adapter, set()).add(decision_rule_version)

    adapters = []
    total_rows = 0
    for adapter, row_count, first_scored_at, last_scored_at in summary_rows:
        count = int(row_count or 0)
        total_rows += count
        adapters.append(
            {
                "adapter_sha256": adapter,
                "row_count": count,
                "model_versions": sorted(model_versions.get(adapter, set())),
                "decision_rule_versions": sorted(
                    decision_rule_versions.get(adapter, set())
                ),
                "first_scored_at": _iso_timestamp(first_scored_at),
                "last_scored_at": _iso_timestamp(last_scored_at),
            }
        )

    adapters.sort(key=lambda item: item["adapter_sha256"])
    return {
        "adapters": adapters,
        "adapter_count": len(adapters),
        "total_rows": total_rows,
    }


adapter_mix = get_adapter_mix


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(bind=test_engine)

    test_session = Session(test_engine)
    test_session.add_all(
        [
            McpLlmAxisScore(
                id=1,
                adapter_sha256="adapter-a",
                axis_name="security",
                model_version="model-1",
                decision_rule_version="rules-1",
                server_id=1,
                scored_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ),
            McpLlmAxisScore(
                id=2,
                adapter_sha256="adapter-a",
                axis_name="quality",
                model_version="model-1",
                decision_rule_version="rules-2",
                server_id=1,
                scored_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            ),
            McpLlmAxisScore(
                id=3,
                adapter_sha256="adapter-b",
                axis_name="security",
                model_version="model-2",
                decision_rule_version="rules-1",
                server_id=2,
                scored_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            ),
        ]
    )
    test_session.commit()

    that_app = FastAPI()

    @that_app.get("/api/scoring/adapter-mix")
    def adapter_mix_endpoint(db: Session = Depends(get_session)):
        return get_adapter_mix(db)

    that_app.dependency_overrides[get_session] = lambda: test_session

    response = TestClient(that_app).get("/api/scoring/adapter-mix")
    assert response.status_code == 200
    payload = response.json()
    assert payload["adapter_count"] == 2
    assert payload["total_rows"] == 3
    assert any(
        adapter["adapter_sha256"] == "adapter-a"
        and adapter["row_count"] == 2
        and adapter["model_versions"]
        for adapter in payload["adapters"]
    )
    print("PASS")