"""Data and computation layer for the unauthenticated-access risk surface."""

from __future__ import annotations

from typing import Dict, List

from sqlalchemy import MetaData, Table, Column, String, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_UNAUTH_ACCESS_TABLE = Table(
    "mcp_unauth_access",
    MetaData(),
    Column("server_id", String),
    Column("url", String),
    Column("unauth_access", String),
    Column("source", String),
    Column("updated_at", String),
)


def fetch_unauth_access(db: Session) -> List[dict]:
    """Fetch unauthenticated-access records joined to the server registry."""
    statement = (
        select(
            _UNAUTH_ACCESS_TABLE.c.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            _UNAUTH_ACCESS_TABLE.c.unauth_access,
            _UNAUTH_ACCESS_TABLE.c.url,
            _UNAUTH_ACCESS_TABLE.c.source,
            _UNAUTH_ACCESS_TABLE.c.updated_at,
        )
        .select_from(
            _UNAUTH_ACCESS_TABLE.outerjoin(
                McpServerRegistry.__table__,
                McpServerRegistry.server_id == _UNAUTH_ACCESS_TABLE.c.server_id,
            )
        )
        .order_by(McpServerRegistry.risk_tier, McpServerRegistry.name)
    )
    return [dict(row) for row in db.execute(statement).mappings().all()]


def list_unauth_access(db: Session) -> List[dict]:
    """Compatibility alias for fetching unauthenticated-access records."""
    return fetch_unauth_access(db)


def get_unauth_access_rows(db: Session) -> List[dict]:
    """Compatibility alias for fetching unauthenticated-access records."""
    return fetch_unauth_access(db)


def summarize_unauth_access(rows: list[dict]) -> dict:
    """Summarize unauthenticated-access records and rank servers by risk tier."""
    by_access_mode: Dict[str, int] = {}
    by_tier: Dict[str, int] = {}
    servers: List[dict] = []

    for row in rows:
        mode = row.get("unauth_access")
        mode_key = str(mode) if mode is not None else "unknown"
        by_access_mode[mode_key] = by_access_mode.get(mode_key, 0) + 1

        tier = row.get("risk_tier")
        tier_key = str(tier) if tier is not None else "unknown"
        by_tier[tier_key] = by_tier.get(tier_key, 0) + 1

        servers.append(
            {
                "server_id": row.get("server_id"),
                "name": row.get("name"),
                "risk_tier": tier,
                "trust_score": row.get("trust_score"),
                "unauth_access": mode,
                "url": row.get("url"),
                "source": row.get("source"),
                "updated_at": row.get("updated_at"),
            }
        )

    servers.sort(
        key=lambda server: (
            "" if server["risk_tier"] is None else str(server["risk_tier"]).lower(),
            "" if server["name"] is None else str(server["name"]).lower(),
        )
    )

    return {
        "total": len(servers),
        "by_access_mode": by_access_mode,
        "by_tier": by_tier,
        "servers": servers,
    }