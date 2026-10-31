import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _days_since_assessed(value: Optional[datetime], now: datetime) -> Optional[int]:
    assessed = _as_utc(value)
    if assessed is None:
        return None
    return max(0, (now - assessed).days)


def get_stale_verdict_reasoning(session: Session) -> dict:
    now = datetime.now(timezone.utc)
    servers = list(session.execute(select(McpServerRegistry)).scalars())

    missing = []
    for server in servers:
        reasoning = server.verdict_reasoning
        if reasoning is None or str(reasoning).strip() == "":
            missing.append(server)

    total_servers = len(servers)
    missing_count = len(missing)
    missing_pct = round((missing_count / total_servers) * 100, 2) if total_servers else 0.0

    tier_groups = {}
    for server in servers:
        tier = server.risk_tier or "unknown"
        group = tier_groups.setdefault(
            tier,
            {"servers": 0, "missing_reasoning": 0},
        )
        group["servers"] += 1
        if server in missing:
            group["missing_reasoning"] += 1

    by_risk_tier = []
    for risk_tier in sorted(tier_groups):
        group = tier_groups[risk_tier]
        servers_count = group["servers"]
        tier_missing = group["missing_reasoning"]
        by_risk_tier.append(
            {
                "risk_tier": risk_tier,
                "servers": servers_count,
                "missing_reasoning": tier_missing,
                "missing_pct": round((tier_missing / servers_count) * 100, 2)
                if servers_count
                else 0.0,
            }
        )

    oldest = []
    for server in missing:
        oldest.append(
            {
                "server_id": server.server_id,
                "name": server.name,
                "verdict": server.verdict,
                "risk_tier": server.risk_tier or "unknown",
                "days_since_assessed": _days_since_assessed(server.last_assessed, now),
            }
        )
    oldest.sort(
        key=lambda item: (
            item["days_since_assessed"] is not None,
            item["days_since_assessed"] or 0,
        ),
        reverse=True,
    )

    return {
        "totals": {
            "servers": total_servers,
            "missing_reasoning": missing_count,
            "missing_pct": missing_pct,
        },
        "by_risk_tier": by_risk_tier,
        "oldest": oldest,
    }


def build_app() -> FastAPI:
    application = FastAPI()

    @application.get("/api/registry/stale-verdict-reasoning")
    def stale_verdict_reasoning(session: Session = Depends(get_session)):
        return get_stale_verdict_reasoning(session)

    return application


app = build_app()


def seed_database(session: Session) -> None:
    now = datetime.now(timezone.utc)
    rows = [
        McpServerRegistry(
            server_id="stale-high-1",
            name="Stale High One",
            verdict="suspicious",
            verdict_reasoning=None,
            confidence=0.72,
            risk_tier="high",
            last_assessed=now - timedelta(days=45),
            last_seen=now - timedelta(days=2),
        ),
        McpServerRegistry(
            server_id="stale-medium-1",
            name="Stale Medium One",
            verdict="unknown",
            verdict_reasoning="",
            confidence=0.41,
            risk_tier="medium",
            last_assessed=now - timedelta(days=20),
            last_seen=now - timedelta(days=1),
        ),
        McpServerRegistry(
            server_id="fresh-low-1",
            name="Fresh Low One",
            verdict="trusted",
            verdict_reasoning="Ownership and registration checks passed.",
            confidence=0.95,
            risk_tier="low",
            last_assessed=now - timedelta(days=3),
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="fresh-medium-1",
            name="Fresh Medium One",
            verdict="approved",
            verdict_reasoning="The service passed the documented review.",
            confidence=0.88,
            risk_tier="medium",
            last_assessed=now - timedelta(days=10),
            last_seen=now - timedelta(hours=3),
        ),
        McpServerRegistry(
            server_id="fresh-low-2",
            name="Fresh Low Two",
            verdict="trusted",
            verdict_reasoning="The published provenance was verified.",
            confidence=0.91,
            risk_tier="low",
            last_assessed=now - timedelta(days=7),
            last_seen=now - timedelta(hours=5),
        ),
    ]
    session.add_all(rows)
    session.commit()


def get_test_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def run_contract() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()

    try:
        seed_database(session)
        test_app = build_app()
        test_app.dependency_overrides[get_session] = lambda: session
        with TestClient(test_app) as client:
            response = client.get("/api/registry/stale-verdict-reasoning")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["totals"]["servers"] == 5
            assert payload["totals"]["missing_reasoning"] == 2
            assert payload["totals"]["missing_pct"] == 40.0
            assert any(
                item["server_id"] == "stale-high-1"
                for item in payload["oldest"]
            )
            assert any(
                item["server_id"] == "stale-medium-1"
                for item in payload["oldest"]
            )
            assert all(
                item["server_id"] not in {"fresh-low-1", "fresh-medium-1", "fresh-low-2"}
                for item in payload["oldest"]
            )
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    run_contract()
    print("PASS")