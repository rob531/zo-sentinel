from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import case, distinct, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def build_source_tier_matrix(
    session: Session,
    registry_source: Optional[str] = None,
    risk_tier: Optional[str] = None,
) -> Dict[str, Any]:
    filters = []
    if registry_source is not None:
        filters.append(McpServerRegistry.registry_source == registry_source)
    if risk_tier is not None:
        filters.append(McpServerRegistry.risk_tier == risk_tier)

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    aggregate_stmt = select(
        McpServerRegistry.registry_source.label("registry_source"),
        func.count().label("server_count"),
        func.avg(McpServerRegistry.trust_score).label("mean_trust_score"),
        func.sum(
            case((McpServerRegistry.last_seen < cutoff, 1), else_=0)
        ).label("stale_count"),
        func.sum(
            case(
                (
                    or_(
                        McpServerRegistry.scan_count == 0,
                        McpServerRegistry.scan_count.is_(None),
                    ),
                    1,
                ),
                else_=0,
            )
        ).label("unscanned_count"),
    ).where(McpServerRegistry.registry_source.is_not(None))

    tier_count_stmt = select(
        McpServerRegistry.registry_source.label("registry_source"),
        McpServerRegistry.risk_tier.label("risk_tier"),
        func.count().label("server_count"),
    ).where(McpServerRegistry.registry_source.is_not(None))

    distinct_tier_stmt = select(
        distinct(McpServerRegistry.risk_tier)
    ).where(McpServerRegistry.risk_tier.is_not(None))

    for condition in filters:
        aggregate_stmt = aggregate_stmt.where(condition)
        tier_count_stmt = tier_count_stmt.where(condition)
        distinct_tier_stmt = distinct_tier_stmt.where(condition)

    aggregate_stmt = aggregate_stmt.group_by(
        McpServerRegistry.registry_source
    ).order_by(McpServerRegistry.registry_source)

    tier_count_stmt = tier_count_stmt.group_by(
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
    )

    aggregate_rows = session.execute(aggregate_stmt).mappings().all()
    tier_rows = session.execute(tier_count_stmt).mappings().all()
    distinct_tiers = session.execute(distinct_tier_stmt).scalars().all()

    tier_names = [str(tier) for tier in distinct_tiers if tier is not None]
    if any(tier is None for tier in distinct_tiers) and "unknown" not in tier_names:
        tier_names.append("unknown")

    tier_counts: Dict[str, Dict[str, int]] = {}
    for row in tier_rows:
        source = str(row["registry_source"])
        tier = row["risk_tier"]
        tier_name = str(tier) if tier is not None else "unknown"
        tier_counts.setdefault(source, {})[tier_name] = int(row["server_count"])

    sources: List[Dict[str, Any]] = []
    for row in aggregate_rows:
        source = str(row["registry_source"])
        counts = {tier: 0 for tier in tier_names}
        counts.update(tier_counts.get(source, {}))

        mean_trust_score = row["mean_trust_score"]
        sources.append(
            {
                "registry_source": source,
                "server_count": int(row["server_count"]),
                "mean_trust_score": (
                    float(mean_trust_score) if mean_trust_score is not None else None
                ),
                "stale_count": int(row["stale_count"] or 0),
                "unscanned_count": int(row["unscanned_count"] or 0),
                "tiers": counts,
            }
        )

    return {"sources": sources}


def get_source_tier_matrix(
    session: Session = Depends(get_session),
    registry_source: Optional[str] = None,
    risk_tier: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    selected_source = registry_source if registry_source is not None else source
    return build_source_tier_matrix(
        session,
        registry_source=selected_source,
        risk_tier=risk_tier,
    )


get_registry_source_tier_matrix = get_source_tier_matrix
get_source_tier_matrix_data = get_source_tier_matrix
get_registry_source_tier_matrix_data = get_source_tier_matrix
source_tier_matrix = get_source_tier_matrix


if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="contract-server-1",
                    registry_source="alpha",
                    risk_tier="high",
                    verdict="trusted",
                    trust_score=0.9,
                    last_seen=now,
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="contract-server-2",
                    registry_source="alpha",
                    risk_tier="low",
                    verdict="review",
                    trust_score=0.7,
                    last_seen=now - timedelta(days=31),
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="contract-server-3",
                    registry_source="beta",
                    risk_tier="low",
                    verdict="trusted",
                    trust_score=0.8,
                    last_seen=now,
                    scan_count=None,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/source-tier-matrix")
    def source_tier_matrix_endpoint(session: Session = Depends(get_session)):
        return get_source_tier_matrix(session=session)

    with TestClient(that_app) as client:
        response = client.get("/api/registry/source-tier-matrix")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["sources"]) == 2
        assert sum(source["server_count"] for source in payload["sources"]) == 3
        for source in payload["sources"]:
            assert sum(source["tiers"].values()) == source["server_count"]

    test_engine.dispose()
    print("PASS")