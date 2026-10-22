from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_tier_histogram(
    session: Session = Depends(get_session),
    registry_source: Optional[str] = None,
) -> Dict[str, Any]:
    source_filter = (
        McpServerRegistry.registry_source == registry_source
        if registry_source is not None
        else None
    )

    tier_query = (
        select(McpServerRegistry.risk_tier, func.count())
        .select_from(McpServerRegistry)
        .group_by(McpServerRegistry.risk_tier)
        .order_by(McpServerRegistry.risk_tier)
    )
    if source_filter is not None:
        tier_query = tier_query.where(source_filter)

    tiers: List[Dict[str, Any]] = [
        {"risk_tier": risk_tier, "count": int(count)}
        for risk_tier, count in session.execute(tier_query).all()
    ]

    total_query = select(func.count()).select_from(McpServerRegistry)
    never_assessed_query = (
        select(func.count())
        .select_from(McpServerRegistry)
        .where(McpServerRegistry.last_assessed.is_(None))
    )
    with_reasoning_query = (
        select(func.count())
        .select_from(McpServerRegistry)
        .where(
            McpServerRegistry.verdict_reasoning.is_not(None),
            func.trim(McpServerRegistry.verdict_reasoning) != "",
        )
    )

    if source_filter is not None:
        total_query = total_query.where(source_filter)
        never_assessed_query = never_assessed_query.where(source_filter)
        with_reasoning_query = with_reasoning_query.where(source_filter)

    total = int(session.execute(total_query).scalar_one())
    never_assessed = int(session.execute(never_assessed_query).scalar_one())
    with_reasoning = int(session.execute(with_reasoning_query).scalar_one())

    return {
        "registry_source": registry_source,
        "total": total,
        "tiers": tiers,
        "never_assessed": never_assessed,
        "with_reasoning": with_reasoning,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


get_registry_tier_histogram = get_tier_histogram


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    tier_values = ["critical", "elevated", "low"]
    source_values = ["github", "internal"]
    now = datetime.now(timezone.utc)
    seeded_never_assessed = 0
    seeded_with_reasoning = 0

    with TestSession() as test_session:
        for index in range(8):
            last_assessed = None
            verdict_reasoning = None
            if index % 4 != 0:
                last_assessed = now - timedelta(days=index)
            if index % 3 == 0:
                verdict_reasoning = "Seeded reasoning"
                seeded_with_reasoning += 1
            if last_assessed is None:
                seeded_never_assessed += 1

            test_session.add(
                McpServerRegistry(
                    server_id=f"histogram-test-{index}",
                    name=f"Histogram Test Server {index}",
                    url=f"https://example.test/server/{index}",
                    registry_source=source_values[index % 2],
                    risk_tier=tier_values[index % 3],
                    last_assessed=last_assessed,
                    trust_score=50.0,
                    confidence=0.9,
                    verdict_reasoning=verdict_reasoning,
                )
            )
        test_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()

    @test_app.get("/api/registry/tier-histogram")
    def tier_histogram_endpoint(
        registry_source: Optional[str] = None,
        session: Session = Depends(get_session),
    ):
        return get_tier_histogram(
            session=session,
            registry_source=registry_source,
        )

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    response = client.get("/api/registry/tier-histogram")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 8
    assert sum(tier["count"] for tier in payload["tiers"]) == 8
    assert payload["never_assessed"] == seeded_never_assessed
    assert payload["with_reasoning"] == seeded_with_reasoning

    filtered_response = client.get(
        "/api/registry/tier-histogram",
        params={"registry_source": "github"},
    )
    assert filtered_response.status_code == 200
    filtered_payload = filtered_response.json()
    assert filtered_payload["registry_source"] == "github"
    assert filtered_payload["total"] == 4
    assert sum(tier["count"] for tier in filtered_payload["tiers"]) == 4

    print("PASS")