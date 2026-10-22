from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


KNOWN_RISK_TIERS = ("low", "medium", "high", "critical")
STALE_AFTER_DAYS = 30


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _normalise_tier(value: Any) -> str:
    if value is None:
        return "unknown"
    tier = str(value).strip().lower()
    return tier if tier in KNOWN_RISK_TIERS else "unknown"


def _is_stale(value: Any, cutoff: datetime) -> bool:
    assessed = _as_datetime(value)
    return assessed is None or assessed < cutoff


def get_risk_tier_mix(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            McpServerRegistry.verdict,
            McpServerRegistry.last_assessed,
            McpServerRegistry.registry_source,
        )
    ).all()

    tier_counts = {tier: 0 for tier in KNOWN_RISK_TIERS}
    tier_counts["unknown"] = 0
    source_counts: dict[str, int] = {}
    verdict_counts: dict[str, int] = {}
    trust_scores: list[float] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_AFTER_DAYS)
    stale_count = 0

    for row in rows:
        tier = _normalise_tier(row.risk_tier)
        tier_counts[tier] += 1

        source = str(row.registry_source).strip() if row.registry_source is not None else "unknown"
        source_counts[source] = source_counts.get(source, 0) + 1

        verdict = str(row.verdict).strip() if row.verdict is not None else "unknown"
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

        if row.trust_score is not None:
            trust_scores.append(float(row.trust_score))

        if _is_stale(row.last_assessed, cutoff):
            stale_count += 1

    server_total = len(rows)
    denominator = server_total or 1
    by_risk_tier = [
        {
            "risk_tier": tier,
            "count": tier_counts[tier],
            "pct": round(tier_counts[tier] * 100.0 / denominator, 2),
        }
        for tier in (*KNOWN_RISK_TIERS, "unknown")
    ]

    mean_trust_score = (
        round(sum(trust_scores) / len(trust_scores), 2) if trust_scores else None
    )

    return {
        "server_total": server_total,
        "by_risk_tier": by_risk_tier,
        "by_source": [
            {"registry_source": source, "count": count}
            for source, count in sorted(source_counts.items())
        ],
        "by_verdict": [
            {"verdict": verdict, "count": count}
            for verdict, count in sorted(verdict_counts.items())
        ],
        "mean_trust_score": mean_trust_score,
        "stale_count": stale_count,
        "stale_pct": round(stale_count * 100.0 / denominator, 2),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


app = FastAPI()


@app.get("/api/registry/risk-tier-mix")
def registry_risk_tier_mix(session: Session = Depends(get_session)) -> dict[str, Any]:
    return get_risk_tier_mix(session)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    now = datetime.now(timezone.utc)
    testing_session.add_all(
        [
            McpServerRegistry(
                server_id="server-low",
                name="Low Risk Server",
                risk_tier="low",
                trust_score=0.91,
                verdict="approved",
                last_assessed=now - timedelta(days=2),
                registry_source="catalogue",
            ),
            McpServerRegistry(
                server_id="server-high",
                name="High Risk Server",
                risk_tier="high",
                trust_score=0.42,
                verdict="review",
                last_assessed=now - timedelta(days=4),
                registry_source="catalogue",
            ),
            McpServerRegistry(
                server_id="server-unknown",
                name="Unknown Tier Server",
                risk_tier=None,
                trust_score=0.67,
                verdict="approved",
                last_assessed=now - timedelta(days=1),
                registry_source="scan",
            ),
            McpServerRegistry(
                server_id="server-stale",
                name="Stale Assessment Server",
                risk_tier="medium",
                trust_score=0.75,
                verdict="review",
                last_assessed=now - timedelta(days=45),
                registry_source="scan",
            ),
        ]
    )
    testing_session.commit()

    def override_get_session() -> Session:
        return testing_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/registry/risk-tier-mix")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["server_total"] == 4
            assert sum(item["count"] for item in payload["by_risk_tier"]) == 4
            unknown = next(
                item for item in payload["by_risk_tier"] if item["risk_tier"] == "unknown"
            )
            assert unknown["count"] == 1
            assert payload["stale_count"] == 1
    finally:
        app.dependency_overrides.pop(get_session, None)
        testing_session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()