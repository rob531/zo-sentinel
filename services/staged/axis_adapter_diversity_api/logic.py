from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db import get_session
from app.models import McpLlmAxisScore
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session


def get_adapter_diversity(session: Session) -> dict[str, Any]:
    grouped_stmt = (
        select(
            McpLlmAxisScore.adapter_sha256.label("adapter_sha256"),
            func.count(McpLlmAxisScore.id).label("rows"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("servers"),
            func.count(func.distinct(McpLlmAxisScore.model_version)).label(
                "model_versions"
            ),
            func.count(func.distinct(McpLlmAxisScore.axis_name)).label("axes"),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .where(
            McpLlmAxisScore.adapter_sha256.is_not(None),
            McpLlmAxisScore.adapter_sha256 != "",
        )
        .group_by(McpLlmAxisScore.adapter_sha256)
        .order_by(McpLlmAxisScore.adapter_sha256)
    )

    adapters = [
        {
            "adapter_sha256": row["adapter_sha256"],
            "rows": int(row["rows"]),
            "servers": int(row["servers"]),
            "model_versions": int(row["model_versions"]),
            "axes": int(row["axes"]),
            "first_scored_at": row["first_scored_at"],
            "last_scored_at": row["last_scored_at"],
        }
        for row in session.execute(grouped_stmt).mappings().all()
    ]

    missing_stmt = select(func.count(McpLlmAxisScore.id)).where(
        or_(
            McpLlmAxisScore.adapter_sha256.is_(None),
            McpLlmAxisScore.adapter_sha256 == "",
        )
    )
    adapter_sha256_missing = int(session.execute(missing_stmt).scalar_one())

    return {
        "generated_at": datetime.now(timezone.utc),
        "adapters": adapters,
        "adapter_sha256_missing": adapter_sha256_missing,
        "distinct_adapters": len(adapters),
    }


get_axis_adapter_diversity = get_adapter_diversity
get_adapter_diversity_data = get_adapter_diversity
get_adapter_diversity_stats = get_adapter_diversity


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False)
    McpLlmAxisScore.__table__.create(test_engine)

    adapter_values = ["adapter-a", "adapter-b", "adapter-c"]
    with TestingSessionLocal() as session:
        for row_id in range(1, 10):
            adapter_index = (row_id - 1) % len(adapter_values)
            session.add(
                McpLlmAxisScore(
                    id=row_id,
                    server_id=f"server-{adapter_index}-{row_id}",
                    axis_name=f"axis-{adapter_index}",
                    label=f"label-{row_id}",
                    label_index=row_id,
                    probs={"axis": 0.75},
                    p_top=0.75,
                    p_critical=0.1,
                    p_danger=0.15,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rules-v1",
                    model_version=f"model-{adapter_index}",
                    adapter_sha256=adapter_values[adapter_index],
                    scored_at=datetime(2025, 1, row_id, tzinfo=timezone.utc),
                )
            )
        session.add(
            McpLlmAxisScore(
                id=10,
                server_id="server-missing",
                axis_name="axis-missing",
                label="label-10",
                label_index=10,
                probs={"axis": 0.5},
                p_top=0.5,
                p_critical=0.25,
                p_danger=0.25,
                escalated=False,
                escalated_to=None,
                decision_rule_version="rules-v1",
                model_version="model-missing",
                adapter_sha256=None,
                scored_at=datetime(2025, 1, 10, tzinfo=timezone.utc),
            )
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/axes/adapter-diversity")
    def adapter_diversity_endpoint(
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_adapter_diversity(session)

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axes/adapter-diversity")
        assert response.status_code == 200
        payload = response.json()
        assert payload["distinct_adapters"] == 3
        assert payload["adapter_sha256_missing"] == 1
        assert len(payload["adapters"]) == 3
        assert sum(adapter["rows"] for adapter in payload["adapters"]) == 9

    print("PASS")