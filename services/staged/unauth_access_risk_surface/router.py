"""Read-only API surface for unauthenticated MCP access risk."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Column, MetaData, String, Table, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import summarize_unauth_access


router = APIRouter(prefix="/api", tags=["unauth-access-risk-surface"])


class UnauthAccessServer(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    trust_score: float | None = None
    unauth_access: str | None = None
    url: str | None = None
    source: str | None = None
    updated_at: str | None = None


class UnauthAccessResponse(BaseModel):
    total: int
    by_access_mode: dict[str, int]
    by_tier: dict[str, int]
    servers: list[UnauthAccessServer]


_unauth_access_metadata = MetaData()
McpUnauthAccess = Table(
    "mcp_unauth_access",
    _unauth_access_metadata,
    Column("server_id", String),
    Column("url", String),
    Column("unauth_access", String),
    Column("source", String),
    Column("updated_at", String),
)


@router.get("/risk/unauth-access", response_model=UnauthAccessResponse)
def get_unauth_access_risk_surface(
    db: Session = Depends(get_session),
) -> UnauthAccessResponse:
    registry = McpServerRegistry.__table__
    statement = (
        select(
            McpUnauthAccess.c.server_id,
            McpUnauthAccess.c.url,
            McpUnauthAccess.c.unauth_access,
            McpUnauthAccess.c.source,
            McpUnauthAccess.c.updated_at,
            registry.c.name,
            registry.c.risk_tier,
            registry.c.trust_score,
        )
        .select_from(
            McpUnauthAccess.outerjoin(
                registry,
                registry.c.server_id == McpUnauthAccess.c.server_id,
            )
        )
    )

    rows = [dict(row) for row in db.execute(statement).mappings().all()]
    return UnauthAccessResponse(**summarize_unauth_access(rows))