"""logic.py -- the service's data/computation layer (one concern, one file).

Mirrors the exemplar doctrine: import the REAL data layer (app.db/app.models),
never an inline/stub/SQLite-dict model. Pure functions over a Session so the
router stays thin and the contract can exercise this directly.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class FreshnessCounts(BaseModel):
    servers: int = 0
    fresh: int = 0
    stale: int = 0
    missing: int = 0


class RiskTierFreshness(BaseModel):
    risk_tier: str
    servers: int = 0
    fresh: int = 0
    stale: int = 0
    missing: int = 0


class StalestServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    age_days: float
    attestation_id: str


class FreshnessLedgerResponse(BaseModel):
    days: int
    overall: FreshnessCounts
    by_risk_tier: List[RiskTierFreshness]
    stalest: List[StalestServer]


router = APIRouter(prefix="/api")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    raise TypeError(f"Unsupported attestation timestamp: {value!r}")


def _attestations_table(db: Session) -> Table:
    """Reflect the real attestation table without defining a stub model."""
    return Table(
        "mcp_attestations",
        MetaData(),
        autoload_with=db.get_bind(),
    )


def _latest_attestations(db: Session) -> Dict[str, Dict[str, Any]]:
    table = _attestations_table(db)
    required = {"id", "server_id", "created_at"}
    available = set(table.c.keys())
    if not required.issubset(available):
        missing = ", ".join(sorted(required - available))
        raise RuntimeError(f"mcp_attestations is missing required columns: {missing}")

    rows = db.execute(
        select(table.c.id, table.c.server_id, table.c.created_at)
    ).mappings().all()
    latest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        server_id = str(row["server_id"])
        created_at = _as_utc(row["created_at"])
        current = latest.get(server_id)
        if current is None or created_at > current["created_at"]:
            latest[server_id] = {
                "attestation_id": str(row["id"]),
                "created_at": created_at,
            }
    return latest


def build_freshness_ledger(db: Session, days: int = 30) -> Dict[str, Any]:
    """Build the freshness ledger using the real registry and attestations."""
    if not 1 <= days <= 365:
        raise ValueError("days must be between 1 and 365")

    now = _utc_now()
    cutoff = now.timestamp() - (days * 86400)
    registry_rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        ).order_by(McpServerRegistry.server_id)
    ).all()
    latest = _latest_attestations(db)

    tiers: Dict[str, Dict[str, int]] = {}
    stalest: List[StalestServer] = []
    overall = {"servers": 0, "fresh": 0, "stale": 0, "missing": 0}

    for server_id_value, name_value, tier_value in registry_rows:
        server_id = str(server_id_value)
        name = str(name_value or "")
        risk_tier = str(tier_value or "unknown")
        counts = tiers.setdefault(
            risk_tier,
            {"servers": 0, "fresh": 0, "stale": 0, "missing": 0},
        )

        overall["servers"] += 1
        counts["servers"] += 1

        attestation = latest.get(server_id)
        if attestation is None:
            overall["missing"] += 1
            counts["missing"] += 1
            continue

        age_days = max(0.0, (now - attestation["created_at"]).total_seconds() / 86400.0)
        is_stale = attestation["created_at"].timestamp() < cutoff
        if is_stale:
            overall["stale"] += 1
            counts["stale"] += 1
            stalest.append(
                StalestServer(
                    server_id=server_id,
                    name=name,
                    risk_tier=risk_tier,
                    age_days=round(age_days, 3),
                    attestation_id=attestation["attestation_id"],
                )
            )
        else:
            overall["fresh"] += 1
            counts["fresh"] += 1

    stalest.sort(key=lambda item: (-item.age_days, item.server_id))
    return {
        "days": days,
        "overall": overall,
        "by_risk_tier": [
            {"risk_tier": tier, **tiers[tier]} for tier in sorted(tiers)
        ],
        "stalest": [item.model_dump() for item in stalest[:25]],
    }


def compute_freshness(db: Session, days: int = 30) -> Dict[str, Any]:
    return build_freshness_ledger(db, days)


def get_attestation_freshness(db: Session, days: int = 30) -> Dict[str, Any]:
    return build_freshness_ledger(db, days)


@router.get("/attestations/freshness", response_model=FreshnessLedgerResponse)
def attestation_freshness(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return build_freshness_ledger(db, days)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, Integer, String, create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)

    attestation_metadata = MetaData()
    attestation_table = Table(
        "mcp_attestations",
        attestation_metadata,
        Column("id", Integer, primary_key=True),
        Column("server_id", String, nullable=False),
        Column("created_at", DateTime, nullable=False),
    )
    attestation_table.create(test_engine)

    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="fresh-server",
                    name="Fresh Server",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="stale-server",
                    name="Stale Server",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="missing-server",
                    name="Missing Server",
                    risk_tier="medium",
                ),
            ]
        )
        session.execute(
            insert(attestation_table).values(
                [
                    {
                        "id": 101,
                        "server_id": "fresh-server",
                        "created_at": datetime.now(timezone.utc) - timedelta(days=1),
                    },
                    {
                        "id": 102,
                        "server_id": "stale-server",
                        "created_at": datetime.now(timezone.utc) - timedelta(days=90),
                    },
                ]
            )
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/attestations/freshness?days=30")
    payload = response.json()
    assert response.status_code == 200
    assert payload["overall"]["servers"] == 3
    assert payload["overall"]["fresh"] == 1
    assert payload["overall"]["stale"] == 1
    assert payload["overall"]["missing"] == 1
    assert len(payload["stalest"]) >= 1
    print("PASS")