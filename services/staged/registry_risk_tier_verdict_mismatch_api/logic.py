from collections import Counter
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


SANCTIONED_RISK_TIERS = {
    "TRUSTED_GENERAL": "low",
    "TRUSTED_RESEARCH": "low",
    "ENTERPRISE_CONTROLLED": "medium",
    "CAUTION_LIMITED": "elevated",
    "HIGH_RISK_ISOLATED": "high",
    "KNOWN_THREAT": "high",
}


def get_registry_risk_tier_verdict_mismatch(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    total_servers = session.execute(
        select(func.count()).select_from(McpServerRegistry)
    ).scalar_one()

    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            McpServerRegistry.last_assessed,
        ).where(
            McpServerRegistry.verdict.is_not(None),
            McpServerRegistry.risk_tier.is_not(None),
        )
    ).all()

    pair_counts: Counter[tuple[str, str]] = Counter()
    sample_server_ids: list[Any] = []

    for row in rows:
        verdict = row.verdict
        risk_tier = row.risk_tier
        if SANCTIONED_RISK_TIERS.get(verdict) != risk_tier:
            pair_counts[(verdict, risk_tier)] += 1
            sample_server_ids.append(row.server_id)

    mismatches = [
        {
            "verdict": verdict,
            "risk_tier": risk_tier,
            "count": count,
        }
        for (verdict, risk_tier), count in sorted(
            pair_counts.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )
    ]

    mismatch_count = sum(pair_counts.values())
    mismatch_pct = (
        round((mismatch_count / total_servers) * 100, 2)
        if total_servers
        else 0.0
    )

    sample_server_ids.sort(key=str)

    return {
        "total_servers": total_servers,
        "mismatches": mismatches,
        "mismatch_count": mismatch_count,
        "mismatch_pct": mismatch_pct,
        "sample_server_ids": sample_server_ids[:10],
    }


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with testing_session() as session:
            yield session

    seeded_rows = [
        ("server-001", "consistent-1", "TRUSTED_GENERAL", "low", 0.95),
        ("server-002", "consistent-2", "ENTERPRISE_CONTROLLED", "medium", 0.85),
        ("server-003", "consistent-3", "CAUTION_LIMITED", "elevated", 0.65),
        ("server-004", "mismatch-1", "TRUSTED_RESEARCH", "medium", 0.80),
        ("server-005", "mismatch-2", "HIGH_RISK_ISOLATED", "low", 0.20),
        ("server-006", "mismatch-3", "KNOWN_THREAT", "elevated", 0.10),
    ]

    with testing_session() as session:
        for server_id, name, verdict, risk_tier, trust_score in seeded_rows:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    verdict=verdict,
                    risk_tier=risk_tier,
                    trust_score=trust_score,
                    last_assessed=datetime.now(timezone.utc),
                )
            )
        session.commit()

    local_app = FastAPI()
    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/registry/risk-tier-verdict-mismatch")
    def registry_risk_tier_verdict_mismatch(
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_registry_risk_tier_verdict_mismatch(session)

    response = TestClient(local_app).get(
        "/api/registry/risk-tier-verdict-mismatch"
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["total_servers"] == 6
    assert payload["mismatch_count"] == 3
    assert payload["mismatch_pct"] == 50.0
    assert payload["mismatches"] == [
        {"verdict": "HIGH_RISK_ISOLATED", "risk_tier": "low", "count": 1},
        {"verdict": "KNOWN_THREAT", "risk_tier": "elevated", "count": 1},
        {"verdict": "TRUSTED_RESEARCH", "risk_tier": "medium", "count": 1},
    ]
    assert payload["sample_server_ids"] == [
        "server-004",
        "server-005",
        "server-006",
    ]

    print("PASS")