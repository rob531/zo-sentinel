from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


def calculate_tier_mix(
    db: Session,
    source: Optional[str] = None,
    days: int = 90,
) -> dict:
    if days < 0:
        raise ValueError("days must be non-negative")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    params = {"source": source, "cutoff": cutoff}

    rows = db.execute(
        text(
            """
            SELECT
                COALESCE(risk_tier, 'UNSCORED') AS tier,
                COALESCE(registry_source, 'UNKNOWN') AS source,
                COUNT(*) AS count
            FROM mcp_server_registry
            WHERE (:source IS NULL OR registry_source = :source)
              AND last_seen >= :cutoff
            GROUP BY
                COALESCE(risk_tier, 'UNSCORED'),
                COALESCE(registry_source, 'UNKNOWN')
            ORDER BY
                COALESCE(registry_source, 'UNKNOWN'),
                COALESCE(risk_tier, 'UNSCORED')
            """
        ),
        params,
    ).all()

    by_tier: Counter[str] = Counter()
    by_source_tier: list[dict] = []

    for row in rows:
        tier = str(row.tier)
        row_source = str(row.source)
        count = int(row.count)
        by_tier[tier] += count
        by_source_tier.append(
            {"source": row_source, "tier": tier, "count": count}
        )

    return {
        "total": sum(by_tier.values()),
        "by_tier": dict(sorted(by_tier.items())),
        "by_source_tier": by_source_tier,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_tier_mix(
    db: Session,
    source: Optional[str] = None,
    days: int = 90,
) -> dict:
    return calculate_tier_mix(db, source=source, days=days)


def get_registry_tier_mix(
    db: Session,
    source: Optional[str] = None,
    days: int = 90,
) -> dict:
    return calculate_tier_mix(db, source=source, days=days)


def _self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    rows = [
        {
            "server_id": "trusted-general",
            "verdict": "allow",
            "risk_tier": "TRUSTED_GENERAL",
            "registry_source": "official",
            "last_seen": now,
            "last_assessed": now,
        },
        {
            "server_id": "trusted-specialist",
            "verdict": "allow_with_conditions",
            "risk_tier": "TRUSTED_SPECIALIST",
            "registry_source": "community",
            "last_seen": now,
            "last_assessed": now,
        },
        {
            "server_id": "blocked",
            "verdict": "block",
            "risk_tier": "HIGH_RISK",
            "registry_source": "official",
            "last_seen": now,
            "last_assessed": now,
        },
        {
            "server_id": "unscored",
            "verdict": "review",
            "risk_tier": None,
            "registry_source": "community",
            "last_seen": now,
            "last_assessed": now,
        },
    ]

    with testing_session() as db:
        db.execute(McpServerRegistry.__table__.insert(), rows)
        db.commit()

    def override_get_session():
        with testing_session() as db:
            yield db

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/tier-mix")
    def tier_mix_endpoint(
        source: Optional[str] = None,
        days: int = 90,
        db: Session = Depends(get_session),
    ):
        return calculate_tier_mix(db, source=source, days=days)

    with TestClient(that_app) as client:
        response = client.get("/api/registry/tier-mix")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == len(rows)
        assert payload["by_tier"]["UNSCORED"] == 1
        assert {
            (group["source"], group["tier"], group["count"])
            for group in payload["by_source_tier"]
        } == {
            ("official", "TRUSTED_GENERAL", 1),
            ("official", "HIGH_RISK", 1),
            ("community", "TRUSTED_SPECIALIST", 1),
            ("community", "UNSCORED", 1),
        }

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()