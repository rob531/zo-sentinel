"""logic.py -- registry risk-tier facet aggregation over the real app data layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_risk_tier_facets(db: Session) -> Dict[str, Any]:
    """Return registry size, tier/source facets, their cross-tab, and untiered count."""
    total = int(
        db.execute(select(func.count()).select_from(McpServerRegistry)).scalar() or 0
    )

    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            func.count(),
        )
        .group_by(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
    ).all()

    by_risk_tier: Dict[str, int] = {}
    by_registry_source: Dict[str, int] = {}
    cross_tab: Dict[str, Dict[str, int]] = {}
    untiered = 0

    for registry_source, risk_tier, count in rows:
        source_key = (registry_source or "unknown").strip() or "unknown"
        tier_key = (risk_tier or "unknown").strip() or "unknown"
        count = int(count)

        by_risk_tier[tier_key] = by_risk_tier.get(tier_key, 0) + count
        by_registry_source[source_key] = by_registry_source.get(source_key, 0) + count
        cross_tab.setdefault(source_key, {})
        cross_tab[source_key][tier_key] = cross_tab[source_key].get(tier_key, 0) + count

        if risk_tier is None or not str(risk_tier).strip():
            untiered += count

    return {
        "total": total,
        "by_risk_tier": by_risk_tier,
        "by_registry_source": by_registry_source,
        "cross_tab": cross_tab,
        "untiered": untiered,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def risk_tier_facets(db: Session) -> Dict[str, Any]:
    """Compatibility entry point for the registry facet service."""
    return get_risk_tier_facets(db)


def get_registry_risk_tier_facets(db: Session) -> Dict[str, Any]:
    """Compatibility entry point for the registry facet service."""
    return get_risk_tier_facets(db)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = SessionLocal()
    db.add_all(
        [
            McpServerRegistry(
                server_id="facet-1",
                name="high",
                registry_source="catalog-a",
                risk_tier="HIGH_RISK_ISOLATED",
            ),
            McpServerRegistry(
                server_id="facet-2",
                name="enterprise",
                registry_source="catalog-a",
                risk_tier="ENTERPRISE_CONTROLLED",
            ),
            McpServerRegistry(
                server_id="facet-3",
                name="trusted",
                registry_source="catalog-b",
                risk_tier="TRUSTED_GENERAL",
            ),
            McpServerRegistry(
                server_id="facet-4",
                name="untiered",
                registry_source="catalog-b",
                risk_tier=None,
            ),
            McpServerRegistry(
                server_id="facet-5",
                name="empty-tier",
                registry_source="catalog-a",
                risk_tier="",
            ),
        ]
    )
    db.commit()

    that_app = FastAPI()

    def override_get_session():
        yield db

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/risk-tier-facets")
    def facets(session: Session = Depends(get_session)):
        return get_risk_tier_facets(session)

    with TestClient(that_app) as client:
        response = client.get("/api/registry/risk-tier-facets")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 5
        assert payload["by_risk_tier"]["HIGH_RISK_ISOLATED"] == 1
        assert payload["untiered"] == 2

    print("PASS")