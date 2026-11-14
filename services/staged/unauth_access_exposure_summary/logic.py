"""Logic for the unauthenticated-access exposure summary service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess


_EXCLUDED_UNAUTH_VALUES = {"none", "None", "null", ""}


def unauth_exposure_summary(session: Session) -> Dict[str, Any]:
    """Aggregate exposed MCP servers and their most recently updated records."""
    total_servers = int(
        session.execute(
            select(func.count(McpServerRegistry.server_id))
        ).scalar_one()
    )

    rows = session.execute(
        select(
            McpUnauthAccess.server_id,
            McpUnauthAccess.url,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpUnauthAccess.updated_at,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .select_from(McpUnauthAccess)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpUnauthAccess.server_id,
        )
        .where(
            McpUnauthAccess.unauth_access.is_not(None),
            McpUnauthAccess.unauth_access.notin_(_EXCLUDED_UNAUTH_VALUES),
        )
        .order_by(McpUnauthAccess.updated_at.desc().nulls_last())
    ).all()

    by_source: Dict[str, int] = {}
    by_risk_tier: Dict[str, int] = {}

    for row in rows:
        source = row.source or "unknown"
        risk_tier = row.risk_tier or "unknown"
        by_source[source] = by_source.get(source, 0) + 1
        by_risk_tier[risk_tier] = by_risk_tier.get(risk_tier, 0) + 1

    top_exposed = [
        {
            "server_id": row.server_id,
            "name": row.name,
            "risk_tier": row.risk_tier,
            "url": row.url,
            "unauth_access": row.unauth_access,
            "source": row.source,
            "updated_at": row.updated_at,
        }
        for row in rows[:10]
    ]

    exposure_pct = (
        float(len(rows) / total_servers * 100) if total_servers else 0.0
    )

    return {
        "exposed_servers": len(rows),
        "total_servers": total_servers,
        "exposure_pct": exposure_pct,
        "by_source": by_source,
        "by_risk_tier": by_risk_tier,
        "top_exposed": top_exposed,
    }


def _run_self_test() -> None:
    """Exercise the real ORM models against a temporary local database."""
    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpUnauthAccess.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    that_app = FastAPI()

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/self-test")
    def self_test_endpoint(session: Session = Depends(get_session)):
        return unauth_exposure_summary(session)

    with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Server One",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Server Two",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="Server Three",
                    risk_tier="low",
                ),
                McpUnauthAccess(
                    server_id="server-1",
                    url="https://one.example",
                    unauth_access="read",
                    source="probe",
                    updated_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
                ),
                McpUnauthAccess(
                    server_id="server-2",
                    url="https://two.example",
                    unauth_access="write",
                    source="probe",
                    updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                ),
                McpUnauthAccess(
                    server_id="server-3",
                    url="https://three.example",
                    unauth_access="none",
                    source="probe",
                    updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

        result = self_test_endpoint(session)

    assert result["exposed_servers"] == 2
    assert result["total_servers"] == 3
    assert len(result["top_exposed"]) == 2
    print("PASS")


if __name__ == "__main__":
    _run_self_test()