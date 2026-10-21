from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/registry", tags=["registry"])


class RegistrySourceStaleness(BaseModel):
    registry_source: str
    total: int
    stale: int
    stale_pct: float


class RegistryTierStaleness(BaseModel):
    risk_tier: str
    total: int
    stale: int


class StaleRegistryServer(BaseModel):
    server_id: str
    name: str
    last_seen: str
    age_days: int
    risk_tier: str
    trust_score: float


class RegistryStalenessResponse(BaseModel):
    days: int
    generated_at: str
    stale_count: int
    total_servers: int
    by_source: List[RegistrySourceStaleness]
    by_tier: List[RegistryTierStaleness]
    stalest: List[StaleRegistryServer]


def _utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(value: Optional[datetime], generated_at: datetime) -> int:
    if value is None:
        return 0
    age = generated_at - _utc_timestamp(value)
    return max(0, age.days)


@router.get("/staleness", response_model=RegistryStalenessResponse)
def get_registry_staleness(
    days: int = Query(default=30),
    limit: int = Query(default=50),
    session: Session = Depends(get_session),
) -> RegistryStalenessResponse:
    days = min(365, max(1, days))
    limit = min(500, max(1, limit))

    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(days=days)

    is_stale = or_(
        McpServerRegistry.last_seen.is_(None),
        McpServerRegistry.last_seen < cutoff,
    )

    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.last_seen,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            is_stale.label("is_stale"),
        )
    ).all()

    source_counts = {}
    tier_counts = {}
    stale_servers = []

    for row in rows:
        registry_source = str(row.registry_source or "unknown")
        risk_tier = str(row.risk_tier or "unknown")
        stale = bool(row.is_stale)

        source = source_counts.setdefault(
            registry_source, {"total": 0, "stale": 0}
        )
        source["total"] += 1
        source["stale"] += int(stale)

        tier = tier_counts.setdefault(risk_tier, {"total": 0, "stale": 0})
        tier["total"] += 1
        tier["stale"] += int(stale)

        if stale:
            last_seen = row.last_seen
            stale_servers.append(
                {
                    "server_id": str(row.server_id),
                    "name": str(row.name or ""),
                    "last_seen": (
                        _utc_timestamp(last_seen).isoformat()
                        if last_seen is not None
                        else ""
                    ),
                    "age_days": _age_days(last_seen, generated_at),
                    "risk_tier": risk_tier,
                    "trust_score": float(row.trust_score or 0.0),
                    "_last_seen_sort": (
                        _utc_timestamp(last_seen)
                        if last_seen is not None
                        else datetime.min.replace(tzinfo=timezone.utc)
                    ),
                }
            )

    stale_servers.sort(
        key=lambda item: (
            -item["age_days"],
            -item["_last_seen_sort"].timestamp(),
            item["server_id"],
        )
    )

    by_source = [
        {
            "registry_source": registry_source,
            "total": counts["total"],
            "stale": counts["stale"],
            "stale_pct": (
                round((counts["stale"] / counts["total"]) * 100.0, 2)
                if counts["total"]
                else 0.0
            ),
        }
        for registry_source, counts in sorted(source_counts.items())
    ]

    by_tier = [
        {
            "risk_tier": risk_tier,
            "total": counts["total"],
            "stale": counts["stale"],
        }
        for risk_tier, counts in sorted(tier_counts.items())
    ]

    stalest = []
    for item in stale_servers[:limit]:
        stalest.append(
            {
                "server_id": item["server_id"],
                "name": item["name"],
                "last_seen": item["last_seen"],
                "age_days": item["age_days"],
                "risk_tier": item["risk_tier"],
                "trust_score": item["trust_score"],
            }
        )

    return RegistryStalenessResponse(
        days=days,
        generated_at=generated_at.isoformat(),
        stale_count=sum(item["stale"] for item in by_source),
        total_servers=len(rows),
        by_source=by_source,
        by_tier=by_tier,
        stalest=stalest,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    registry_source TEXT,
                    last_seen DATETIME,
                    risk_tier TEXT,
                    trust_score FLOAT
                )
                """
            )
        )

        seed_time = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id,
                    name,
                    registry_source,
                    last_seen,
                    risk_tier,
                    trust_score
                ) VALUES (
                    :server_id,
                    :name,
                    :registry_source,
                    :last_seen,
                    :risk_tier,
                    :trust_score
                )
                """
            ),
            [
                {
                    "server_id": "server-aged",
                    "name": "Aged Server",
                    "registry_source": "source-a",
                    "last_seen": (
                        seed_time - timedelta(days=60)
                    ).strftime("%Y-%m-%d %H:%M:%S.%f"),
                    "risk_tier": "high",
                    "trust_score": 0.25,
                },
                {
                    "server_id": "server-fresh",
                    "name": "Fresh Server",
                    "registry_source": "source-b",
                    "last_seen": (
                        seed_time - timedelta(days=5)
                    ).strftime("%Y-%m-%d %H:%M:%S.%f"),
                    "risk_tier": "low",
                    "trust_score": 0.95,
                },
                {
                    "server_id": "server-never-seen",
                    "name": "Never Seen Server",
                    "registry_source": "source-a",
                    "last_seen": None,
                    "risk_tier": "medium",
                    "trust_score": 0.5,
                },
            ],
        )

    def override_get_session():
        with TestingSessionLocal() as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router, prefix="/api")

    with TestClient(that_app) as client:
        response = client.get(
            "/api/registry/staleness",
            params={"days": 30, "limit": 50},
        )
        assert response.status_code == 200
        payload = response.json()

        assert payload["stale_count"] == 2
        assert payload["total_servers"] == 3
        assert len(payload["by_source"]) == 2
        assert {item["server_id"] for item in payload["stalest"]} == {
            "server-aged",
            "server-never-seen",
        }

        ages = [item["age_days"] for item in payload["stalest"]]
        assert ages == sorted(ages, reverse=True)

    print("PASS")