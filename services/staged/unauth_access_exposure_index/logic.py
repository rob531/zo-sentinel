"""Data and computation layer for unauthenticated-access exposure reporting."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess


def list_unauth_access(
    db: Session,
    limit: int = 200,
    tier: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List exposed MCP servers ordered by ascending registry trust score."""
    bounded_limit = max(1, min(int(limit), 1000))

    statement = (
        select(
            McpUnauthAccess.server_id,
            McpUnauthAccess.url,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpUnauthAccess.updated_at,
            McpServerRegistry.name,
            McpServerRegistry.trust_score,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
        )
        .select_from(McpUnauthAccess)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpUnauthAccess.server_id,
        )
        .where(
            or_(
                McpUnauthAccess.unauth_access.is_(None),
                McpUnauthAccess.unauth_access != "authenticated",
            )
        )
        .order_by(McpServerRegistry.trust_score.asc().nulls_last())
        .limit(bounded_limit)
    )

    if tier is not None:
        statement = statement.where(McpServerRegistry.risk_tier == tier)

    result = db.execute(statement).all()
    return [
        {
            "server_id": row.server_id,
            "name": row.name,
            "url": row.url,
            "unauth_access": row.unauth_access,
            "source": row.source,
            "updated_at": row.updated_at,
            "trust_score": row.trust_score,
            "verdict": row.verdict,
            "risk_tier": row.risk_tier,
        }
        for row in result
    ]


def list_unauth_access_rows(
    db: Session,
    limit: int = 200,
    tier: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Compatibility alias for the exposure row query."""
    return list_unauth_access(db, limit=limit, tier=tier)


def get_unauth_access_exposure(
    db: Session,
    limit: int = 200,
    tier: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the complete response payload for the exposure endpoint."""
    rows = list_unauth_access(db, limit=limit, tier=tier)
    return {"count": len(rows), "rows": rows}


if __name__ == "__main__":
    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine, checkfirst=True)
    McpUnauthAccess.__table__.create(test_engine, checkfirst=True)

    TestingSession = sessionmaker(bind=test_engine)
    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-alpha",
                    name="Alpha MCP",
                    url="https://alpha.example/mcp",
                    trust_score=40,
                    verdict="review",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="server-beta",
                    name="Beta MCP",
                    url="https://beta.example/mcp",
                    trust_score=70,
                    verdict="accept",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="server-gamma",
                    name="Gamma MCP",
                    url="https://gamma.example/mcp",
                    trust_score=90,
                    verdict="accept",
                    risk_tier="low",
                ),
            ]
        )
        session.add_all(
            [
                McpUnauthAccess(
                    server_id="server-alpha",
                    url="https://alpha.example/mcp",
                    unauth_access="unauthenticated",
                    source="self-test",
                ),
                McpUnauthAccess(
                    server_id="server-beta",
                    url="https://beta.example/mcp",
                    unauth_access="unauthenticated",
                    source="self-test",
                ),
                McpUnauthAccess(
                    server_id="server-gamma",
                    url="https://gamma.example/mcp",
                    unauth_access="authenticated",
                    source="self-test",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/exposure/unauth-access")
    def unauth_access_endpoint(
        limit: int = Query(default=200, ge=1, le=1000),
        tier: Optional[str] = Query(default=None),
        db: Session = Depends(get_session),
    ):
        return get_unauth_access_exposure(db, limit=limit, tier=tier)

    with TestClient(test_app) as client:
        response = client.get("/api/exposure/unauth-access")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 2
        assert len(payload["rows"]) == 2
        assert any(
            row["server_id"] == "server-alpha" and row["risk_tier"] == "high"
            for row in payload["rows"]
        )

    print("PASS")