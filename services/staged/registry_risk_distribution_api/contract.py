from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

STALE_THRESHOLD_HOURS = 24

router = APIRouter(prefix="/api")


class RegistryDistributionResponse(BaseModel):
    total_servers: int
    by_risk_tier: dict[str, int]
    by_verdict: dict[str, int]
    by_registry_source: dict[str, int]
    never_assessed_count: int
    stale_count: int
    stale_threshold_hours: int
    generated_at: str


def _distribution_key(value: Any) -> str:
    if value is None:
        return "unknown"
    return str(value)


def _build_distribution(session: Session, registry_source: str | None) -> dict[str, Any]:
    conditions = []
    if registry_source is not None:
        conditions.append(McpServerRegistry.registry_source == registry_source)

    total_servers = (
        session.scalar(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(*conditions)
        )
        or 0
    )

    grouped = session.execute(
        select(
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
            McpServerRegistry.registry_source,
            func.count().label("server_count"),
        )
        .where(*conditions)
        .group_by(
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
            McpServerRegistry.registry_source,
        )
    ).all()

    by_risk_tier: dict[str, int] = {}
    by_verdict: dict[str, int] = {}
    by_registry_source: dict[str, int] = {}

    for risk_tier, verdict, source, server_count in grouped:
        risk_key = _distribution_key(risk_tier)
        verdict_key = _distribution_key(verdict)
        source_key = _distribution_key(source)

        by_risk_tier[risk_key] = by_risk_tier.get(risk_key, 0) + server_count
        by_verdict[verdict_key] = by_verdict.get(verdict_key, 0) + server_count
        by_registry_source[source_key] = by_registry_source.get(source_key, 0) + server_count

    never_assessed_count = (
        session.scalar(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(*conditions, McpServerRegistry.last_assessed.is_(None))
        )
        or 0
    )

    cutoff = datetime.utcnow() - timedelta(hours=STALE_THRESHOLD_HOURS)
    stale_count = (
        session.scalar(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(
                *conditions,
                McpServerRegistry.last_assessed.is_not(None),
                McpServerRegistry.last_assessed < cutoff,
            )
        )
        or 0
    )

    return {
        "total_servers": int(total_servers),
        "by_risk_tier": by_risk_tier,
        "by_verdict": by_verdict,
        "by_registry_source": by_registry_source,
        "never_assessed_count": int(never_assessed_count),
        "stale_count": int(stale_count),
        "stale_threshold_hours": STALE_THRESHOLD_HOURS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def registry_distribution(registry_source: str | None = None) -> dict[str, Any]:
    dependency = get_session()
    session = next(dependency)
    try:
        return _build_distribution(session, registry_source)
    finally:
        close = getattr(dependency, "close", None)
        if close is not None:
            close()


@router.get("/registry/distribution", response_model=RegistryDistributionResponse)
def get_registry_distribution(
    registry_source: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _build_distribution(session, registry_source)


def main() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.utcnow()
    rows = [
        {
            "server_id": "contract-1",
            "verdict": "approved",
            "risk_tier": "high",
            "registry_source": "github",
            "last_assessed": now,
            "trust_score": 0.95,
            "confidence": 0.98,
        },
        {
            "server_id": "contract-2",
            "verdict": "flagged",
            "risk_tier": "medium",
            "registry_source": "github",
            "last_assessed": now - timedelta(hours=48),
            "trust_score": 0.55,
            "confidence": 0.75,
        },
        {
            "server_id": "contract-3",
            "verdict": "rejected",
            "risk_tier": "low",
            "registry_source": "github",
            "last_assessed": None,
            "trust_score": 0.20,
            "confidence": 0.60,
        },
        {
            "server_id": "contract-4",
            "verdict": "approved",
            "risk_tier": "high",
            "registry_source": "gitlab",
            "last_assessed": now,
            "trust_score": 0.93,
            "confidence": 0.97,
        },
        {
            "server_id": "contract-5",
            "verdict": "flagged",
            "risk_tier": "medium",
            "registry_source": "gitlab",
            "last_assessed": now,
            "trust_score": 0.51,
            "confidence": 0.72,
        },
        {
            "server_id": "contract-6",
            "verdict": "rejected",
            "risk_tier": "low",
            "registry_source": "gitlab",
            "last_assessed": now,
            "trust_score": 0.18,
            "confidence": 0.58,
        },
    ]

    with testing_session() as session:
        session.add_all(McpServerRegistry(**row) for row in rows)
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/distribution")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 6
        assert sum(payload["by_risk_tier"].values()) == 6
        assert payload["never_assessed_count"] == 1
        assert len(payload["by_registry_source"]) == 2

        filtered = client.get(
            "/api/registry/distribution",
            params={"registry_source": "github"},
        )
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert filtered_payload["total_servers"] == 3
        assert sum(filtered_payload["by_risk_tier"].values()) == 3

    print("PASS")


if __name__ == "__main__":
    main()