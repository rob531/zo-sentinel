"""HTTP surface for registry scan-backlog aging metrics."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import scan_backlog_aging

router = APIRouter(prefix="/api", tags=["registry"])


class AgingBand(BaseModel):
    band: str
    count: int


class RegistrySourceAging(BaseModel):
    registry_source: str | None
    count: int
    never_scanned: int


class StaleRegistryServer(BaseModel):
    server_id: str
    name: str | None
    registry_source: str | None
    last_scanned: datetime | None
    days_since_scan: int


class ScanBacklogAgingResponse(BaseModel):
    days: int
    generated_at: datetime
    bands: list[AgingBand]
    by_source: list[RegistrySourceAging]
    stalest: list[StaleRegistryServer]


@router.get(
    "/registry/scan-backlog-aging",
    response_model=ScanBacklogAgingResponse,
)
def get_scan_backlog_aging(
    days: int = 30,
    db: Session = Depends(get_session),
) -> ScanBacklogAgingResponse:
    result: dict[str, Any] = scan_backlog_aging(db, days)
    return ScanBacklogAgingResponse(
        days=result.get("days", days),
        generated_at=result.get("generated_at", datetime.now(timezone.utc)),
        bands=result["bands"],
        by_source=result["by_source"],
        stalest=result["stalest"],
    )