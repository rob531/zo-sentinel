"""Logic and FastAPI routes for the unauthenticated-access matrix service."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess


router = APIRouter(prefix="/unauth", tags=["unauth-access"])


def _timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _source(value: Optional[str]) -> str:
    return value or "unknown"


def build_access_matrix(db: Session) -> Dict[str, Any]:
    """Build the access matrix from registered servers and access records."""
    total_servers = int(
        db.execute(select(func.count()).select_from(McpServerRegistry)).scalar_one()
    )

    rows = db.execute(
        select(
            McpUnauthAccess.source,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.updated_at,
        )
        .select_from(McpServerRegistry)
        .outerjoin(
            McpUnauthAccess,
            McpUnauthAccess.server_id == McpServerRegistry.server_id,
        )
    ).all()

    by_source: Dict[str, int] = {}
    by_access: Dict[str, int] = {}
    cells: Dict[tuple[str, str], int] = {}

    for source, unauth_access, _updated_at in rows:
        source_key = _source(source)
        access_key = unauth_access or "unknown"
        by_source[source_key] = by_source.get(source_key, 0) + 1
        by_access[access_key] = by_access.get(access_key, 0) + 1
        cells[(source_key, access_key)] = cells.get((source_key, access_key), 0) + 1

    updated_at_max = db.execute(
        select(func.max(McpUnauthAccess.updated_at))
    ).scalar_one_or_none()

    return {
        "total_servers": total_servers,
        "by_source": [
            {"source": source, "count": count}
            for source, count in sorted(by_source.items())
        ],
        "by_access": [
            {"unauth_access": access, "count": count}
            for access, count in sorted(by_access.items())
        ],
        "cells": [
            {"source": source, "unauth_access": access, "count": count}
            for (source, access), count in sorted(cells.items())
        ],
        "updated_at_max": _timestamp(updated_at_max) or "",
    }


def get_server_detail(db: Session, server_id: str) -> Dict[str, Any]:
    """Return registry display fields and access records for one server."""
    registry = db.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    if registry is None:
        raise HTTPException(status_code=404, detail="Server not found")

    records = db.execute(
        select(
            McpUnauthAccess.url,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpUnauthAccess.updated_at,
        )
        .where(McpUnauthAccess.server_id == server_id)
        .order_by(
            McpUnauthAccess.source,
            McpUnauthAccess.url,
            McpUnauthAccess.updated_at,
        )
    ).all()

    return {
        "server_id": registry.server_id,
        "name": registry.name,
        "risk_tier": registry.risk_tier,
        "verdict": registry.verdict,
        "records": [
            {
                "url": url or "",
                "unauth_access": unauth_access or "unknown",
                "source": _source(source),
                "updated_at": _timestamp(updated_at),
            }
            for url, unauth_access, source, updated_at in records
        ],
    }


@router.get("/matrix")
def matrix(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Return the unauthenticated-access matrix for registered MCP servers."""
    return build_access_matrix(db)


@router.get("/servers/{server_id}")
def server_detail(
    server_id: str, db: Session = Depends(get_session)
) -> Dict[str, Any]:
    """Return unauthenticated-access details for one registered MCP server."""
    return get_server_detail(db, server_id)


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
    McpServerRegistry.metadata.create_all(
        engine,
        tables=[McpServerRegistry.__table__, McpUnauthAccess.__table__],
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seeded = [
        ("server-a", "Alpha", "alpha", "open", "https://alpha.example/mcp"),
        ("server-b", "Beta", "alpha", "closed", "https://beta.example/mcp"),
        ("server-c", "Gamma", "beta", "open", "https://gamma.example/mcp"),
    ]

    with TestingSessionLocal() as session:
        for server_id, name, source, access, url in seeded:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    registry_source=source,
                )
            )
            session.add(
                McpUnauthAccess(
                    server_id=server_id,
                    source=source,
                    unauth_access=access,
                    url=url,
                )
            )
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router, prefix="/api")

    with TestClient(test_app) as client:
        matrix_response = client.get("/api/unauth/matrix")
        assert matrix_response.status_code == 200
        matrix_data = matrix_response.json()
        assert matrix_data["total_servers"] == 3
        assert any(
            cell["source"] == "alpha"
            and cell["unauth_access"] == "open"
            and cell["count"] == 1
            for cell in matrix_data["cells"]
        )

        detail_response = client.get("/api/unauth/servers/server-a")
        assert detail_response.status_code == 200
        detail_data = detail_response.json()
        assert detail_data["records"][0]["url"] == "https://alpha.example/mcp"

    print("PASS")