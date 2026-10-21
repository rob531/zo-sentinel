from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_registry_staleness as _logic_get_registry_staleness
except ImportError:
    _logic_get_registry_staleness = None


router = APIRouter()


class SourceStaleness(BaseModel):
    registry_source: str
    total: int
    stale: int
    stale_pct: float


class TierStaleness(BaseModel):
    risk_tier: str
    total: int
    stale: int


class StaleServer(BaseModel):
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
    by_source: list[SourceStaleness]
    by_tier: list[TierStaleness]
    stalest: list[StaleServer]


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _serialize_last_seen(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _age_days(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, datetime):
        observed = _as_utc_naive(value)
        cutoff = datetime.utcnow()
        return max(0, int((cutoff - observed).total_seconds() // 86400))
    return 0


def _query_staleness(session: Session, days: int, limit: int) -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(days=days)

    all_rows = session.execute(
        text(
            """
            SELECT server_id, name, registry_source, last_seen, risk_tier, trust_score
            FROM mcp_server_registry
            """
        )
    ).mappings().all()

    stale_rows = session.execute(
        text(
            """
            SELECT server_id, name, registry_source, last_seen, risk_tier, trust_score
            FROM mcp_server_registry
            WHERE last_seen IS NULL OR last_seen < :cutoff
            """
        ),
        {"cutoff": cutoff},
    ).mappings().all()

    source_totals: dict[str, dict[str, int]] = {}
    tier_totals: dict[str, dict[str, int]] = {}
    stale_server_ids: set[str] = set()

    for row in all_rows:
        source = str(row["registry_source"] or "")
        tier = str(row["risk_tier"] or "")
        source_totals.setdefault(source, {"total": 0, "stale": 0})
        tier_totals.setdefault(tier, {"total": 0, "stale": 0})
        source_totals[source]["total"] += 1
        tier_totals[tier]["total"] += 1

    for row in stale_rows:
        source = str(row["registry_source"] or "")
        tier = str(row["risk_tier"] or "")
        stale_server_ids.add(str(row["server_id"]))
        source_totals.setdefault(source, {"total": 0, "stale": 0})
        tier_totals.setdefault(tier, {"total": 0, "stale": 0})
        source_totals[source]["stale"] += 1
        tier_totals[tier]["stale"] += 1

    by_source = [
        {
            "registry_source": source,
            "total": values["total"],
            "stale": values["stale"],
            "stale_pct": (
                float(values["stale"]) / values["total"] * 100.0
                if values["total"]
                else 0.0
            ),
        }
        for source, values in sorted(source_totals.items())
    ]

    by_tier = [
        {
            "risk_tier": tier,
            "total": values["total"],
            "stale": values["stale"],
        }
        for tier, values in sorted(tier_totals.items())
    ]

    stale_servers = [
        {
            "server_id": str(row["server_id"]),
            "name": str(row["name"] or ""),
            "last_seen": _serialize_last_seen(row["last_seen"]),
            "age_days": _age_days(row["last_seen"]),
            "risk_tier": str(row["risk_tier"] or ""),
            "trust_score": float(row["trust_score"] or 0.0),
        }
        for row in stale_rows
    ]
    stale_servers.sort(key=lambda item: item["age_days"], reverse=True)

    return {
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stale_count": len(stale_servers),
        "total_servers": len(all_rows),
        "by_source": by_source,
        "by_tier": by_tier,
        "stalest": stale_servers[:limit],
    }


@router.get(
    "/registry/staleness",
    response_model=RegistryStalenessResponse,
)
def registry_staleness(
    days: int = Query(30),
    limit: int = Query(50),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    days = max(1, min(365, days))
    limit = max(1, min(500, limit))
    return _query_staleness(session, days, limit)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    registry_source TEXT,
                    last_seen TIMESTAMP,
                    last_scanned TIMESTAMP,
                    risk_tier TEXT,
                    trust_score REAL,
                    verdict TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, last_seen, risk_tier, trust_score, verdict)
                VALUES
                    (:id1, :name1, :source1, :seen1, :tier1, :trust1, :verdict1),
                    (:id2, :name2, :source2, :seen2, :tier2, :trust2, :verdict2),
                    (:id3, :name3, :source3, NULL, :tier3, :trust3, :verdict3)
                """
            ),
            {
                "id1": "aged",
                "name1": "Aged server",
                "source1": "source-a",
                "seen1": datetime.utcnow() - timedelta(days=60),
                "tier1": "high",
                "trust1": 0.8,
                "verdict1": "active",
                "id2": "fresh",
                "name2": "Fresh server",
                "source2": "source-b",
                "seen2": datetime.utcnow() - timedelta(days=5),
                "tier2": "low",
                "trust2": 0.9,
                "verdict2": "active",
                "id3": "unknown",
                "name3": "Unknown server",
                "source3": "source-a",
                "tier3": "medium",
                "trust3": 0.7,
                "verdict3": "unknown",
            },
        )

    testing_session = sessionmaker(bind=engine)()
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_get_session():
        yield testing_session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/staleness?days=30&limit=50")
        assert response.status_code == 200
        payload = response.json()
        assert payload["stale_count"] == 2
        assert payload["total_servers"] == 3
        assert len(payload["by_source"]) == 2
        assert payload["stalest"][0]["age_days"] >= payload["stalest"][1]["age_days"]

    print("PASS")


if __name__ == "__main__":
    _self_test()