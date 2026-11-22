"""Data and computation layer for the dispute tier-shift distribution service."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


_TIER_ORDER = {
    "none": 0,
    "minimal": 1,
    "low": 2,
    "medium": 3,
    "moderate": 3,
    "high": 4,
    "critical": 5,
    "severe": 5,
}


def _normalize_tier(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def _classify_shift(proposed_risk: Any, current_tier: Any) -> str:
    proposed = _normalize_tier(proposed_risk)
    current = _normalize_tier(current_tier)

    if proposed not in _TIER_ORDER or current not in _TIER_ORDER:
        return "unknown_tier"

    proposed_rank = _TIER_ORDER[proposed]
    current_rank = _TIER_ORDER[current]

    if proposed_rank > current_rank:
        return "upgrade"
    if proposed_rank < current_rank:
        return "downgrade"
    return "lateral"


def get_tier_shift_distribution(db: Session) -> Dict[str, Any]:
    """Summarize disputes and classify open disputes by registry tier shift."""
    rows = db.execute(
        select(
            McpScoreDispute.server_id,
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.proposed_overall_risk,
            McpServerRegistry.risk_tier,
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
    ).all()

    by_status: Counter[str] = Counter()
    by_reason_category: Counter[str] = Counter()
    server_counts: Counter[str] = Counter()
    tier_shift = {
        "upgrade": 0,
        "downgrade": 0,
        "lateral": 0,
        "unknown_tier": 0,
    }

    for server_id, status, reason_category, proposed_risk, current_tier in rows:
        status_key = str(status) if status is not None else "unknown"
        reason_key = (
            str(reason_category) if reason_category is not None else "unknown"
        )
        server_key = str(server_id)

        by_status[status_key] += 1
        by_reason_category[reason_key] += 1
        server_counts[server_key] += 1

        if status_key.strip().lower() == "open":
            shift = _classify_shift(proposed_risk, current_tier)
            tier_shift[shift] += 1

    top_disputed_servers = [
        {"server_id": server_id, "count": count}
        for server_id, count in sorted(
            server_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]

    return {
        "total": len(rows),
        "by_status": dict(by_status),
        "by_reason_category": dict(by_reason_category),
        "tier_shift": tier_shift,
        "top_disputed_servers": top_disputed_servers,
    }


def compute_tier_shift_distribution(db: Session) -> Dict[str, Any]:
    """Compatibility alias for the tier-shift distribution computation."""
    return get_tier_shift_distribution(db)


router = APIRouter(tags=["disputes"])


@router.get("/api/disputes/tier-shift-distribution")
def tier_shift_distribution(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_tier_shift_distribution(db)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    McpScoreDispute.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="server-shared",
                    name="Shared server",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="server-other",
                    name="Other server",
                    risk_tier="high",
                ),
                McpScoreDispute(
                    id=1,
                    server_id="server-shared",
                    submitted_by="contract-user",
                    proposed_overall_risk="high",
                    proposed_axes={},
                    reason_category="risk_understated",
                    status="open",
                    explanation="The proposed risk is higher than the registry tier.",
                ),
                McpScoreDispute(
                    id=2,
                    server_id="server-shared",
                    submitted_by="contract-user",
                    proposed_overall_risk="medium",
                    proposed_axes={},
                    reason_category="tier_confirmed",
                    status="open",
                    explanation="The proposed risk matches the registry tier.",
                ),
                McpScoreDispute(
                    id=3,
                    server_id="server-missing",
                    submitted_by="contract-user",
                    proposed_overall_risk="high",
                    proposed_axes={},
                    reason_category="missing_registry",
                    status="open",
                    explanation="The server has no registry entry.",
                ),
            ]
        )
        db.commit()

        that_app = FastAPI()
        that_app.include_router(router)

        def override_get_session():
            yield db

        that_app.dependency_overrides[get_session] = override_get_session

        with TestClient(that_app) as client:
            response = client.get("/api/disputes/tier-shift-distribution")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 3
            assert payload["tier_shift"]["upgrade"] == 1
            assert payload["tier_shift"]["unknown_tier"] == 1
            assert payload["top_disputed_servers"][0]["count"] == 2

    print("PASS")