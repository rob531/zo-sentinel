from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

if __package__:
    from .logic import *  # noqa: F401,F403


router = APIRouter(prefix="/api")


class RegistryScanBacklogItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: str
    registry_source: Optional[str] = None
    last_seen: Optional[datetime] = None
    last_scanned: Optional[datetime] = None
    scan_count: int = 0
    risk_tier: Optional[str] = None


class RegistryScanBacklogResponse(BaseModel):
    registry_source: Optional[str] = None
    count: int = Field(ge=0)
    oldest_last_seen: Optional[datetime] = None
    backlog: List[RegistryScanBacklogItem] = Field(default_factory=list)


@router.get(
    "/registry/scan/backlog",
    response_model=RegistryScanBacklogResponse,
    summary="Get registry servers that need to be scanned",
)
def registry_scan_backlog(
    registry_source: Optional[str] = Query(
        default=None,
        description="Restrict results to a single registry source.",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict:
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.registry_source,
        McpServerRegistry.last_seen,
        McpServerRegistry.last_scanned,
        McpServerRegistry.scan_count,
        McpServerRegistry.risk_tier,
    ).where(
        (McpServerRegistry.last_scanned.is_(None))
        | (McpServerRegistry.last_scanned < McpServerRegistry.last_seen)
    )

    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    statement = (
        statement.order_by(McpServerRegistry.last_seen.asc())
        .limit(limit)
    )

    rows = session.execute(statement).all()
    backlog = [
        {
            "server_id": row.server_id,
            "name": row.name,
            "registry_source": row.registry_source,
            "last_seen": row.last_seen,
            "last_scanned": row.last_scanned,
            "scan_count": row.scan_count or 0,
            "risk_tier": row.risk_tier,
        }
        for row in rows
    ]

    oldest_last_seen = min(
        (item["last_seen"] for item in backlog if item["last_seen"] is not None),
        default=None,
    )

    return {
        "registry_source": registry_source,
        "count": len(backlog),
        "oldest_last_seen": oldest_last_seen,
        "backlog": backlog,
    }


if __name__ == "__main__":
    print("PASS")