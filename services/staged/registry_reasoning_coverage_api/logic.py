from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


SessionDependency = Annotated[Session, Depends(get_session)]


def _clamp_days(days: int) -> int:
    try:
        normalized = int(days)
    except (TypeError, ValueError):
        normalized = 30
    return max(1, min(365, normalized))


def _coverage_pct(covered: int, total: int) -> float:
    if total == 0:
        return 0.0
    return round((covered / total) * 100.0, 2)


def compute_registry_reasoning_coverage(
    session: Session,
    days: int = 30,
) -> dict[str, Any]:
    normalized_days = _clamp_days(days)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=normalized_days)

    last_assessed_type = McpServerRegistry.__table__.c.last_assessed.type
    if not getattr(last_assessed_type, "timezone", False):
        cutoff = cutoff.replace(tzinfo=None)

    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict_reasoning,
        )
        .where(McpServerRegistry.last_assessed >= cutoff)
        .order_by(McpServerRegistry.risk_tier, McpServerRegistry.server_id)
    )
    rows = session.execute(statement).all()

    tier_counts: dict[str, dict[str, int]] = {}
    missing_server_ids: list[str] = []

    for row in rows:
        raw_tier = row.risk_tier
        tier = str(raw_tier) if raw_tier is not None else "unknown"
        reasoning = row.verdict_reasoning
        has_reasoning = isinstance(reasoning, str) and bool(reasoning.strip())

        counts = tier_counts.setdefault(
            tier,
            {"total": 0, "with_reasoning": 0, "missing_reasoning": 0},
        )
        counts["total"] += 1
        if has_reasoning:
            counts["with_reasoning"] += 1
        else:
            counts["missing_reasoning"] += 1
            if row.server_id is not None:
                missing_server_ids.append(str(row.server_id))

    tiers = []
    for tier in sorted(tier_counts, key=str.casefold):
        counts = tier_counts[tier]
        tiers.append(
            {
                "tier": tier,
                "total": counts["total"],
                "with_reasoning": counts["with_reasoning"],
                "missing_reasoning": counts["missing_reasoning"],
                "coverage_pct": _coverage_pct(
                    counts["with_reasoning"],
                    counts["total"],
                ),
            }
        )

    scored_total = sum(item["total"] for item in tiers)
    covered_total = sum(item["with_reasoning"] for item in tiers)

    return {
        "tiers": tiers,
        "summary": {
            "scored_total": scored_total,
            "covered_total": covered_total,
            "coverage_pct": _coverage_pct(covered_total, scored_total),
            "missing_reasoning_sample": sorted(missing_server_ids)[:25],
        },
    }


compute_reasoning_coverage = compute_registry_reasoning_coverage
get_registry_reasoning_coverage = compute_registry_reasoning_coverage
get_reasoning_coverage = compute_registry_reasoning_coverage
compute_verdict_reasoning_coverage = compute_registry_reasoning_coverage
get_verdict_reasoning_coverage = compute_registry_reasoning_coverage
build_registry_reasoning_coverage = compute_registry_reasoning_coverage


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_test_engine = None
    from sqlalchemy import create_engine

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    assessed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestSession() as session:
        session.execute(
            insert(McpServerRegistry.__table__),
            [
                {
                    "server_id": "server-a",
                    "risk_tier": "high",
                    "verdict": "malicious",
                    "verdict_reasoning": "Observed an unsafe command execution path.",
                    "last_assessed": assessed_at,
                    "first_seen": assessed_at,
                },
                {
                    "server_id": "server-b",
                    "risk_tier": "high",
                    "verdict": "safe",
                    "verdict_reasoning": None,
                    "last_assessed": assessed_at,
                    "first_seen": assessed_at,
                },
                {
                    "server_id": "server-c",
                    "risk_tier": "medium",
                    "verdict": "safe",
                    "verdict_reasoning": "   \n\t ",
                    "last_assessed": assessed_at,
                    "first_seen": assessed_at,
                },
                {
                    "server_id": "server-d",
                    "risk_tier": "medium",
                    "verdict": "malicious",
                    "verdict_reasoning": "The registry description contained prompt injection.",
                    "last_assessed": assessed_at,
                    "first_seen": assessed_at,
                },
            ],
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/verdict-reasoning-coverage")
    def registry_reasoning_coverage_endpoint(
        days: int = 30,
        session: SessionDependency = None,
    ) -> dict[str, Any]:
        result = compute_registry_reasoning_coverage(session, days)
        return {
            "days": _clamp_days(days),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **result,
        }

    test_app.dependency_overrides[get_session] = lambda: TestSession()

    with TestClient(test_app) as client:
        response = client.get(
            "/api/registry/verdict-reasoning-coverage",
            params={"days": 30},
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["tiers"]) == 2
    assert sum(tier["missing_reasoning"] for tier in payload["tiers"]) == 2
    assert all(tier["coverage_pct"] == 50.0 for tier in payload["tiers"])
    assert "summary" in payload
    assert payload["summary"]["scored_total"] == 4
    assert payload["summary"]["covered_total"] == 2
    assert payload["summary"]["coverage_pct"] == 50.0
    assert sorted(payload["summary"]["missing_reasoning_sample"]) == [
        "server-b",
        "server-c",
    ]
    print("PASS")


if __name__ == "__main__":
    _self_test()