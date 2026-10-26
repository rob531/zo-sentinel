from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")

VERDICT_TO_RISK_TIER = {
    "TRUSTED_GENERAL": "low",
    "TRUSTED_RESEARCH": "low",
    "ENTERPRISE_CONTROLLED": "medium",
    "CAUTION_LIMITED": "medium",
    "HIGH_RISK_ISOLATED": "high",
    "KNOWN_THREAT": "high",
    "INSUFFICIENT": "high",
}

_TIER_ALIASES = {
    "low": "low",
    "low_risk": "low",
    "tier_1": "low",
    "tier1": "low",
    "medium": "medium",
    "medium_risk": "medium",
    "tier_2": "medium",
    "tier2": "medium",
    "high": "high",
    "high_risk": "high",
    "tier_3": "high",
    "tier3": "high",
}


def _normalized_tier(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return _TIER_ALIASES.get(token)


def _divergent(stored_tier: Any, expected_tier: str) -> bool:
    normalized = _normalized_tier(stored_tier)
    return normalized is None or normalized != expected_tier


@router.get("/registry/verdict-tier-divergence")
def registry_verdict_tier_divergence(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    rows = session.execute(
        text(
            """
            SELECT
                server_id,
                name,
                verdict,
                risk_tier,
                confidence,
                last_assessed
            FROM mcp_server_registry
            ORDER BY server_id
            """
        )
    ).mappings().all()

    divergent: list[dict[str, Any]] = []
    pair_counts: Counter[tuple[Any, Any]] = Counter()

    for row in rows:
        verdict = row["verdict"]
        stored_tier = row["risk_tier"]
        pair_counts[(verdict, stored_tier)] += 1

        expected_tier = VERDICT_TO_RISK_TIER.get(verdict)
        if expected_tier is None or _divergent(stored_tier, expected_tier):
            divergent.append(
                {
                    "server_id": row["server_id"],
                    "name": row["name"],
                    "verdict": verdict,
                    "risk_tier": stored_tier,
                    "expected_tier": expected_tier,
                    "confidence": row["confidence"],
                    "last_assessed": row["last_assessed"],
                }
            )

    by_pair = [
        {
            "verdict": verdict,
            "risk_tier": risk_tier,
            "count": count,
        }
        for (verdict, risk_tier), count in sorted(
            pair_counts.items(),
            key=lambda item: (str(item[0][0]), str(item[0][1])),
        )
    ]

    return {
        "total": len(rows),
        "divergent": divergent,
        "divergent_count": len(divergent),
        "by_pair": by_pair,
    }


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(engine)

    with session_factory() as seed_session:
        seed_session.add_all(
            [
                McpServerRegistry(
                    server_id="matching",
                    name="Matching server",
                    verdict="TRUSTED_GENERAL",
                    risk_tier="low",
                    confidence=0.99,
                    last_assessed=datetime(2025, 1, 1, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="divergent",
                    name="Divergent server",
                    verdict="KNOWN_THREAT",
                    risk_tier="low",
                    confidence=0.95,
                    last_assessed=datetime(2025, 1, 2, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="insufficient",
                    name="Insufficient server",
                    verdict="INSUFFICIENT",
                    risk_tier="high",
                    confidence=0.10,
                    last_assessed=datetime(2025, 1, 3, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="null-tier",
                    name="Null tier server",
                    verdict="TRUSTED_RESEARCH",
                    risk_tier=None,
                    confidence=0.50,
                    last_assessed=datetime(2025, 1, 4, tzinfo=timezone.utc),
                ),
            ]
        )
        seed_session.commit()

    def override_session():
        with session_factory() as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/verdict-tier-divergence")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 4
        assert payload["divergent_count"] == 2
        assert {item["server_id"] for item in payload["divergent"]} == {
            "divergent",
            "null-tier",
        }
        pair_counts = {
            (item["verdict"], item["risk_tier"]): item["count"]
            for item in payload["by_pair"]
        }
        assert pair_counts[("TRUSTED_GENERAL", "low")] == 1

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()