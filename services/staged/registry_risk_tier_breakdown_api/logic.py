from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpServerRegistry


RISK_TIERS = (
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
    "UNSET",
)


class RiskTierCount(BaseModel):
    risk_tier: str
    count: int
    pct: float
    avg_trust_score: float


class RegistrySourceCount(BaseModel):
    registry_source: str
    count: int


class RegistryRiskTierBreakdown(BaseModel):
    generated_at: datetime
    total_servers: int
    registry_source: Optional[str] = None
    tiers: List[RiskTierCount] = Field(default_factory=list)
    sources: List[RegistrySourceCount] = Field(default_factory=list)


def _normalise_risk_tier(value: Any) -> str:
    if value is None:
        return "UNSET"
    text = str(value).strip()
    if not text:
        return "UNSET"
    return text


def _normalise_source(value: Any) -> str:
    if value is None:
        return "UNSET"
    text = str(value).strip()
    return text or "UNSET"


def _breakdown(
    db: Any,
    registry_source: Optional[str] = None,
) -> Dict[str, Any]:
    from sqlalchemy import case, func, or_, select

    conditions = []
    if registry_source is not None:
        conditions.append(McpServerRegistry.registry_source == registry_source)

    tier_expression = case(
        (
            or_(
                McpServerRegistry.risk_tier.is_(None),
                McpServerRegistry.risk_tier == "",
            ),
            "UNSET",
        ),
        else_=McpServerRegistry.risk_tier,
    )

    tier_query = select(
        tier_expression.label("risk_tier"),
        func.count(McpServerRegistry.server_id).label("count"),
        func.avg(McpServerRegistry.trust_score).label("avg_trust_score"),
    )
    source_query = select(
        McpServerRegistry.registry_source.label("registry_source"),
        func.count(McpServerRegistry.server_id).label("count"),
    )

    if conditions:
        tier_query = tier_query.where(*conditions)
        source_query = source_query.where(*conditions)

    tier_rows = db.execute(
        tier_query.group_by(tier_expression)
    ).all()
    source_rows = db.execute(
        source_query.group_by(McpServerRegistry.registry_source)
    ).all()

    total_servers = sum(int(row.count or 0) for row in tier_rows)
    tier_values: Dict[str, Dict[str, Any]] = {}

    for row in tier_rows:
        tier = _normalise_risk_tier(row.risk_tier)
        if tier not in RISK_TIERS:
            continue
        tier_values[tier] = {
            "count": int(row.count or 0),
            "avg_trust_score": round(float(row.avg_trust_score or 0), 2),
        }

    tiers = []
    for tier in RISK_TIERS:
        values = tier_values.get(tier, {"count": 0, "avg_trust_score": 0.0})
        count = values["count"]
        tiers.append(
            {
                "risk_tier": tier,
                "count": count,
                "pct": round((count / total_servers) * 100, 2)
                if total_servers
                else 0.0,
                "avg_trust_score": values["avg_trust_score"],
            }
        )

    sources = [
        {
            "registry_source": _normalise_source(row.registry_source),
            "count": int(row.count or 0),
        }
        for row in sorted(
            source_rows,
            key=lambda item: (-int(item.count or 0), _normalise_source(item.registry_source)),
        )
    ]

    return {
        "generated_at": datetime.now(timezone.utc),
        "total_servers": total_servers,
        "registry_source": registry_source,
        "tiers": tiers,
        "sources": sources,
    }


def get_risk_tier_breakdown(
    db: Any = Depends(get_session),
    registry_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Read and aggregate the registry risk-tier breakdown for the app database."""
    return _breakdown(db, registry_source)


def compute_risk_tier_breakdown(
    db: Any,
    registry_source: Optional[str] = None,
) -> Dict[str, Any]:
    return _breakdown(db, registry_source)


def fetch_risk_tier_breakdown(
    db: Any,
    registry_source: Optional[str] = None,
) -> Dict[str, Any]:
    return _breakdown(db, registry_source)


def build_risk_tier_breakdown(
    db: Any,
    registry_source: Optional[str] = None,
) -> Dict[str, Any]:
    return _breakdown(db, registry_source)


def get_registry_risk_tier_breakdown(
    db: Any = Depends(get_session),
    registry_source: Optional[str] = None,
) -> Dict[str, Any]:
    return _breakdown(db, registry_source)


risk_tier_breakdown = get_risk_tier_breakdown


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
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_db = TestSession()
    test_db.add_all(
        [
            McpServerRegistry(
                server_id="one",
                registry_source="source-a",
                risk_tier="TRUSTED_GENERAL",
                verdict="approved",
                trust_score=90,
                name="one",
                url="https://one.invalid",
            ),
            McpServerRegistry(
                server_id="two",
                registry_source="source-a",
                risk_tier="TRUSTED_GENERAL",
                verdict="approved",
                trust_score=80,
                name="two",
                url="https://two.invalid",
            ),
            McpServerRegistry(
                server_id="three",
                registry_source="source-b",
                risk_tier="HIGH_RISK_ISOLATED",
                verdict="blocked",
                trust_score=20,
                name="three",
                url="https://three.invalid",
            ),
            McpServerRegistry(
                server_id="four",
                registry_source="source-b",
                risk_tier=None,
                verdict="unknown",
                trust_score=None,
                name="four",
                url="https://four.invalid",
            ),
        ]
    )
    test_db.commit()

    test_app = FastAPI()

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/risk-tier-breakdown")
    def test_endpoint(registry_source: Optional[str] = None, db: Any = Depends(get_session)):
        return get_risk_tier_breakdown(db=db, registry_source=registry_source)

    response = TestClient(test_app).get("/api/registry/risk-tier-breakdown")
    payload = response.json()
    assert response.status_code == 200
    assert payload["total_servers"] == 4
    assert next(tier for tier in payload["tiers"] if tier["risk_tier"] == "TRUSTED_GENERAL")["count"] == 2
    assert next(tier for tier in payload["tiers"] if tier["risk_tier"] == "UNSET")["count"] == 1
    assert len(payload["tiers"]) == 8
    print("PASS")