"""Data and computation layer for unauthenticated-exposure tier joins."""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess


def unauth_exposure_tier_join(db: Session) -> Dict[str, Any]:
    """Join unauthenticated-access records to registry servers and aggregate them."""
    joined_rows = db.execute(
        select(McpServerRegistry, McpUnauthAccess)
        .join(McpUnauthAccess, McpServerRegistry.server_id == McpUnauthAccess.server_id)
    ).all()

    tier_servers: Dict[str, set[str]] = {}
    access_servers: Dict[Any, set[str]] = {}

    for server, access in joined_rows:
        tier = server.risk_tier or "unknown"
        tier_servers.setdefault(tier, set()).add(server.server_id)
        access_servers.setdefault(access.unauth_access, set()).add(server.server_id)

    by_tier = {
        tier: {
            "servers_with_unauth": len(servers),
            "n_distinct_servers": len(servers),
        }
        for tier, servers in tier_servers.items()
    }

    by_access_value = [
        {"unauth_access": unauth_access, "servers": len(servers)}
        for unauth_access, servers in sorted(
            access_servers.items(), key=lambda item: str(item[0])
        )
    ]

    ordered_rows = sorted(
        joined_rows,
        key=lambda item: (
            item[1].unauth_access is None,
            str(item[1].unauth_access or ""),
        ),
        reverse=True,
    )
    ordered_rows = sorted(
        ordered_rows,
        key=lambda item: (
            item[0].trust_score is None,
            item[0].trust_score if item[0].trust_score is not None else 0,
        ),
    )
    ordered_rows = sorted(
        ordered_rows,
        key=lambda item: item[0].server_id,
    )

    top_exposed = [
        {
            "server_id": server.server_id,
            "name": server.name,
            "risk_tier": server.risk_tier,
            "trust_score": server.trust_score,
            "unauth_access": access.unauth_access,
        }
        for server, access in ordered_rows[:10]
    ]

    total_servers = int(
        db.execute(select(func.count()).select_from(McpServerRegistry)).scalar_one()
    )

    return {
        "by_tier": by_tier,
        "by_access_value": by_access_value,
        "top_exposed": top_exposed,
        "total_servers": total_servers,
    }


def get_unauth_exposure_tier_join(db: Session) -> Dict[str, Any]:
    """Compatibility wrapper for the service computation."""
    return unauth_exposure_tier_join(db)


def build_unauth_exposure_tier_join(db: Session) -> Dict[str, Any]:
    """Compatibility wrapper for the service computation."""
    return unauth_exposure_tier_join(db)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    local_app = FastAPI()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(engine)
    McpUnauthAccess.__table__.create(engine)

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session

    with TestingSessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Server One",
                    risk_tier="high",
                    trust_score=40,
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Server Two",
                    risk_tier="high",
                    trust_score=80,
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="Server Three",
                    risk_tier="low",
                    trust_score=95,
                ),
            ]
        )
        session.add_all(
            [
                McpUnauthAccess(
                    server_id="server-1",
                    url="https://server-1.example",
                    unauth_access="admin",
                    source="seed",
                ),
                McpUnauthAccess(
                    server_id="server-2",
                    url="https://server-2.example",
                    unauth_access="read",
                    source="seed",
                ),
            ]
        )
        session.commit()

        result = unauth_exposure_tier_join(session)
        assert result["total_servers"] == 3
        assert result["by_tier"]["high"]["servers_with_unauth"] == 2
        assert len(result["top_exposed"]) == 2

    print("PASS")