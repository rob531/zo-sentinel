from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


VERDICT_VALUES = [
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
]


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "value"):
        value = value.value
    return str(value)


def get_verdict_histogram(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Return the registry verdict and risk-tier distribution."""
    total_servers = int(
        session.execute(select(func.count()).select_from(McpServerRegistry)).scalar()
        or 0
    )

    verdict_rows = session.execute(
        select(McpServerRegistry.verdict, func.count())
        .select_from(McpServerRegistry)
        .group_by(McpServerRegistry.verdict)
    ).all()
    verdict_counts: Dict[str, int] = {value: 0 for value in VERDICT_VALUES}
    for verdict, count in verdict_rows:
        verdict = _as_text(verdict)
        if verdict is not None:
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + int(count)

    risk_rows = session.execute(
        select(McpServerRegistry.risk_tier, func.count())
        .select_from(McpServerRegistry)
        .group_by(McpServerRegistry.risk_tier)
    ).all()
    risk_counts: Dict[str, int] = {}
    for risk_tier, count in risk_rows:
        risk_tier = _as_text(risk_tier)
        if risk_tier is not None:
            risk_counts[risk_tier] = int(count)

    never_assessed = int(
        session.execute(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(McpServerRegistry.last_assessed.is_(None))
        ).scalar()
        or 0
    )

    freshness_cutoff = datetime.utcnow() - timedelta(hours=24)
    stale_over_24h = int(
        session.execute(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(McpServerRegistry.last_seen < freshness_cutoff)
        ).scalar()
        or 0
    )

    def share(count: int) -> float:
        return float(count) / float(total_servers) if total_servers else 0.0

    verdicts: List[Dict[str, Any]] = [
        {
            "verdict": verdict,
            "count": count,
            "share": share(count),
        }
        for verdict, count in verdict_counts.items()
    ]
    risk_tiers: List[Dict[str, Any]] = [
        {
            "risk_tier": risk_tier,
            "count": count,
            "share": share(count),
        }
        for risk_tier, count in sorted(risk_counts.items())
    ]

    return {
        "total_servers": total_servers,
        "verdicts": verdicts,
        "risk_tiers": risk_tiers,
        "never_assessed": never_assessed,
        "stale_over_24h": stale_over_24h,
    }


get_registry_verdict_histogram = get_verdict_histogram
get_histogram = get_verdict_histogram


if __name__ == "__main__":
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
    McpServerRegistry.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    rows = [
        McpServerRegistry(
            server_id="self-test-1",
            name="Self test 1",
            verdict="TRUSTED_GENERAL",
            risk_tier="LOW",
            last_assessed=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="self-test-2",
            name="Self test 2",
            verdict="TRUSTED_GENERAL",
            risk_tier="LOW",
            last_assessed=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="self-test-3",
            name="Self test 3",
            verdict="TRUSTED_RESEARCH",
            risk_tier="MEDIUM",
            last_assessed=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="self-test-4",
            name="Self test 4",
            verdict="TRUSTED_RESEARCH",
            risk_tier="MEDIUM",
            last_assessed=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="self-test-5",
            name="Self test 5",
            verdict="CAUTION_LIMITED",
            risk_tier="HIGH",
            last_assessed=None,
            last_seen=now,
        ),
    ]

    with SessionLocal() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/verdict-histogram")
    def verdict_histogram(session: Session = Depends(get_session)):
        return get_verdict_histogram(session)

    response = TestClient(that_app).get("/api/registry/verdict-histogram")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 5
    assert sum(item["count"] for item in payload["verdicts"]) == 5
    assert payload["never_assessed"] == 1
    print("PASS")