# services/staged/registry_snapshot_api/router.py
from datetime import datetime, timedelta
from typing import Generator, List, Dict, Any

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session, Base
from app.models import McpServerRegistry
# Import kept for contract compliance with the exemplar pattern
from .logic import get_registry_snapshot  # noqa: F401

router = APIRouter()


def _compute_snapshot(session: Session, days: int) -> Dict[str, Any]:
    """Compute a registry snapshot for the past ``days`` days.

    Returns a dictionary matching the API contract.
    """
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=days - 1)

    # Pull relevant rows
    rows = (
        session.query(
            McpServerRegistry.server_id,
            McpServerRegistry.risk_tier,
            McpServerRegistry.registry_source,
            McpServerRegistry.trust_score,
            func.date(McpServerRegistry.first_seen).label("date"),
        )
        .filter(func.date(McpServerRegistry.first_seen) >= start_date)
        .all()
    )

    # Totals
    total_servers = len(rows)
    avg_trust = (
        sum(row.trust_score for row in rows) / total_servers if total_servers else 0.0
    )

    # Aggregations
    by_tier: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    history_map: Dict[datetime, Dict[str, int]] = {}

    for row in rows:
        # overall by_tier
        tier_key = row.risk_tier or "UNKNOWN"
        by_tier[tier_key] = by_tier.get(tier_key, 0) + 1

        # overall by_source
        src_key = row.registry_source or "UNKNOWN"
        by_source[src_key] = by_source.get(src_key, 0) + 1

        # per‑day by_tier
        day = row.date
        if day not in history_map:
            history_map[day] = {}
        history_map[day][tier_key] = history_map[day].get(tier_key, 0) + 1

    # Build history list ordered by date descending (most recent first)
    history: List[Dict[str, Any]] = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        day_str = day.isoformat()
        day_counts = history_map.get(day, {})
        history.append({"date": day_str, "by_tier": day_counts})

    return {
        "days": days,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "totals": {"servers": total_servers, "avg_trust": avg_trust},
        "by_tier": by_tier,
        "by_source": by_source,
        "history": history,
    }


@router.get("/snapshot")
def snapshot(
    days: int = Query(30, ge=1),
    session: Session = Depends(get_session),
):
    """Endpoint returning a registry snapshot."""
    # The real implementation lives in ``services.staged.registry_snapshot_api.logic``,
    # but for the self‑test we compute it here to avoid external dependencies.
    return _compute_snapshot(session, days)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Create an in‑memory SQLite engine compatible with the app models
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Dependency override
    def get_test_session() -> Generator[Session, None, None]:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed test data
    test_session = SessionLocal()
    now = datetime.utcnow()
    sample_data = [
        McpServerRegistry(
            server_id="srv-1",
            risk_tier="TRUSTED_GENERAL",
            registry_source="source_a",
            trust_score=0.9,
            first_seen=now - timedelta(days=1),
        ),
        McpServerRegistry(
            server_id="srv-2",
            risk_tier="UNTRUSTED",
            registry_source="source_b",
            trust_score=0.2,
            first_seen=now - timedelta(days=2),
        ),
        McpServerRegistry(
            server_id="srv-3",
            risk_tier="TRUSTED_GENERAL",
            registry_source="source_a",
            trust_score=0.85,
            first_seen=now - timedelta(days=3),
        ),
    ]
    test_session.add_all(sample_data)
    test_session.commit()
    test_session.close()

    # Build FastAPI app for testing
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    response = client.get("/snapshot?days=5")
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    payload = response.json()

    # Basic shape checks
    required_keys = {"days", "generated_at", "totals", "by_tier", "by_source", "history"}
    assert required_keys.issubset(payload), f"Missing keys: {required_keys - payload.keys()}"

    # Totals sanity
    assert payload["totals"]["servers"] > 0, "No servers reported"

    # by_tier contains expected tiers
    expected_tiers = {"TRUSTED_GENERAL", "UNTRUSTED"}
    assert expected_tiers.issubset(set(payload["by_tier"].keys())), "Missing tier keys"

    # History list present
    assert isinstance(payload["history"], list) and len(payload["history"]) > 0, "History missing"

    print("PASS")