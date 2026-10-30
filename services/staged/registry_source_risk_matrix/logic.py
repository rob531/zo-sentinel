from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import case, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


def _normalise_call(session_or_days: Any, days_or_session: Any) -> tuple[Any, int]:
    if isinstance(session_or_days, int):
        return days_or_session, session_or_days
    if isinstance(days_or_session, int):
        return session_or_days, days_or_session
    return session_or_days, 30


def _source_risk_matrix(session: Any, days: int = 30) -> dict[str, Any]:
    days = max(0, int(days))
    cutoff = datetime.utcnow() - timedelta(days=days)

    source_expr = func.coalesce(McpServerRegistry.registry_source, "unknown")
    tier_expr = func.coalesce(McpServerRegistry.risk_tier, "unknown")

    tier_rows = session.execute(
        select(
            source_expr.label("registry_source"),
            tier_expr.label("risk_tier"),
            func.count(McpServerRegistry.server_id).label("server_count"),
        )
        .group_by(source_expr, tier_expr)
    ).all()

    source_rows = session.execute(
        select(
            source_expr.label("registry_source"),
            func.count(McpServerRegistry.server_id).label("total"),
            func.avg(McpServerRegistry.trust_score).label("mean_trust_score"),
            func.avg(McpServerRegistry.scan_count).label("mean_scan_count"),
            func.coalesce(
                func.sum(
                    case(
                        (McpServerRegistry.last_seen < cutoff, 1),
                        else_=0,
                    )
                ),
                0,
            ).label("stale_count"),
        )
        .group_by(source_expr)
    ).all()

    tiers_by_source: dict[str, dict[str, int]] = {}
    for row in tier_rows:
        source = row.registry_source
        tier = row.risk_tier
        tiers_by_source.setdefault(source, {})[tier] = int(row.server_count or 0)

    sources: list[dict[str, Any]] = []
    for row in sorted(source_rows, key=lambda item: item.registry_source):
        sources.append(
            {
                "registry_source": row.registry_source,
                "total": int(row.total or 0),
                "mean_trust_score": float(row.mean_trust_score or 0.0),
                "mean_scan_count": float(row.mean_scan_count or 0.0),
                "stale_count": int(row.stale_count or 0),
                "tiers": tiers_by_source.get(row.registry_source, {}),
            }
        )

    return {
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
    }


def get_registry_source_risk_matrix(
    session_or_days: Any = None,
    days_or_session: Any = 30,
    db: Any = None,
) -> dict[str, Any]:
    if db is not None:
        session_or_days = db
    session, days = _normalise_call(session_or_days, days_or_session)
    if session is None:
        raise ValueError("A database session is required")
    return _source_risk_matrix(session, days)


def get_source_risk_matrix(
    session_or_days: Any = None,
    days_or_session: Any = 30,
    db: Any = None,
) -> dict[str, Any]:
    return get_registry_source_risk_matrix(session_or_days, days_or_session, db)


def build_source_risk_matrix(
    session_or_days: Any = None,
    days_or_session: Any = 30,
    db: Any = None,
) -> dict[str, Any]:
    return get_registry_source_risk_matrix(session_or_days, days_or_session, db)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    seeded = [
        McpServerRegistry(
            server_id="registry-test-1",
            registry_source="github",
            risk_tier="high",
            trust_score=0.90,
            scan_count=4,
            last_seen=now,
            last_assessed=now,
        ),
        McpServerRegistry(
            server_id="registry-test-2",
            registry_source="github",
            risk_tier="medium",
            trust_score=0.70,
            scan_count=6,
            last_seen=now,
            last_assessed=now,
        ),
        McpServerRegistry(
            server_id="registry-test-3",
            registry_source="npm",
            risk_tier="low",
            trust_score=0.80,
            scan_count=2,
            last_seen=now,
            last_assessed=now,
        ),
        McpServerRegistry(
            server_id="registry-test-4",
            registry_source="npm",
            risk_tier="high",
            trust_score=0.60,
            scan_count=8,
            last_seen=now - timedelta(days=60),
            last_assessed=now,
        ),
    ]

    session = SessionLocal()
    session.add_all(seeded)
    session.commit()

    def override_get_session():
        test_session = SessionLocal()
        try:
            yield test_session
        finally:
            test_session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/source-risk-matrix")
    def source_risk_matrix(
        days: int = 30,
        db: Session = Depends(get_session),
    ):
        return get_registry_source_risk_matrix(db, days)

    from fastapi.testclient import TestClient

    response = TestClient(test_app).get("/api/registry/source-risk-matrix?days=30")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["sources"]) == 2

    by_source = {item["registry_source"]: item for item in payload["sources"]}
    assert sum(by_source["github"]["tiers"].values()) == by_source["github"]["total"]
    assert sum(by_source["npm"]["tiers"].values()) == by_source["npm"]["total"]
    assert abs(by_source["github"]["mean_trust_score"] - 0.80) < 0.001
    assert abs(by_source["npm"]["mean_trust_score"] - 0.70) < 0.001

    print("PASS")