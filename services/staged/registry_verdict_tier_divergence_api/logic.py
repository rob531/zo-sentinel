from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")

VERDICT_TO_EXPECTED_TIER = {
    "TRUSTED_GENERAL": "TRUSTED",
    "TRUSTED_RESEARCH": "TRUSTED",
    "ENTERPRISE_CONTROLLED": "TRUSTED",
    "CAUTION_LIMITED": "CAUTION",
    "HIGH_RISK_ISOLATED": "HIGH_RISK",
    "KNOWN_THREAT": "HIGH_RISK",
    "INSUFFICIENT": "INSUFFICIENT",
}


def derive_expected_tier(verdict: Optional[str]) -> str:
    if verdict is None:
        return "INSUFFICIENT"
    return VERDICT_TO_EXPECTED_TIER.get(verdict.strip().upper(), "INSUFFICIENT")


@router.get("/registry/verdict-tier-divergence")
def get_verdict_tier_divergence(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.confidence,
            McpServerRegistry.last_assessed,
        ).order_by(McpServerRegistry.server_id)
    ).all()

    divergent: List[Dict[str, Any]] = []
    pair_counts: Dict[Any, int] = {}

    for row in rows:
        pair = (row.verdict, row.risk_tier)
        pair_counts[pair] = pair_counts.get(pair, 0) + 1

        expected_tier = derive_expected_tier(row.verdict)
        if row.risk_tier != expected_tier:
            divergent.append(
                {
                    "server_id": row.server_id,
                    "name": row.name,
                    "verdict": row.verdict,
                    "risk_tier": row.risk_tier,
                    "expected_tier": expected_tier,
                    "confidence": row.confidence,
                    "last_assessed": row.last_assessed,
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
            key=lambda item: (
                item[0][0] is None,
                item[0][0] or "",
                item[0][1] is None,
                item[0][1] or "",
            ),
        )
    ]

    return {
        "total": len(rows),
        "divergent": divergent,
        "divergent_count": len(divergent),
        "by_pair": by_pair,
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    seed_rows = [
        McpServerRegistry(
            server_id="matching",
            name="Matching server",
            verdict="TRUSTED_GENERAL",
            risk_tier="TRUSTED",
            confidence=0.95,
            last_assessed=datetime.now(timezone.utc),
        ),
        McpServerRegistry(
            server_id="divergent",
            name="Divergent server",
            verdict="CAUTION_LIMITED",
            risk_tier="HIGH_RISK",
            confidence=0.80,
            last_assessed=datetime.now(timezone.utc),
        ),
        McpServerRegistry(
            server_id="insufficient",
            name="Insufficient server",
            verdict="INSUFFICIENT",
            risk_tier="INSUFFICIENT",
            confidence=0.20,
            last_assessed=datetime.now(timezone.utc),
        ),
        McpServerRegistry(
            server_id="null-tier",
            name="Null tier server",
            verdict="TRUSTED_GENERAL",
            risk_tier=None,
            confidence=0.50,
            last_assessed=datetime.now(timezone.utc),
        ),
    ]

    with TestSession() as session:
        session.add_all(seed_rows)
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/verdict-tier-divergence")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 4
    assert payload["divergent_count"] == 2
    assert any(
        item["verdict"] == "TRUSTED_GENERAL"
        and item["risk_tier"] == "TRUSTED"
        and item["count"] == 1
        for item in payload["by_pair"]
    )
    assert any(item["server_id"] == "null-tier" for item in payload["divergent"])
    print("PASS")