from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_source_lane_throughput as _get_source_lane_throughput
except ImportError:  # pragma: no cover - supports direct execution of this module
    try:
        from .logic import (
            get_registry_source_lane_throughput as _get_source_lane_throughput,
        )
    except ImportError:
        from services.staged.registry_source_lane_throughput_api.logic import (
            get_source_lane_throughput as _get_source_lane_throughput,
        )


router = APIRouter()


class SourceLaneTierCounts(BaseModel):
    """Risk-tier counts for a registry source lane."""

    tiers: Dict[str, int] = Field(default_factory=dict)


class SourceLaneThroughput(BaseModel):
    """Throughput statistics for one registry source."""

    registry_source: str
    first_seen: int = 0
    last_seen: int = 0
    scanned: int = 0
    mean_scan_count: float = 0.0
    tiers: Dict[str, int] = Field(default_factory=dict)


class SourceLaneThroughputTotals(BaseModel):
    """Totals across all registry source lanes."""

    servers: int = 0


class SourceLaneThroughputResponse(BaseModel):
    """Response returned by the registry source-lane throughput endpoint."""

    days: int
    lanes: List[SourceLaneThroughput] = Field(default_factory=list)
    totals: SourceLaneThroughputTotals = Field(default_factory=SourceLaneThroughputTotals)


@router.get(
    "/api/registry/source-lanes",
    response_model=SourceLaneThroughputResponse,
    summary="Get registry source-lane throughput",
)
def get_registry_source_lane_throughput(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> SourceLaneThroughputResponse:
    """Return server throughput grouped by registry source over the requested window."""
    result = _get_source_lane_throughput(session, days)

    if isinstance(result, SourceLaneThroughputResponse):
        return result
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    return SourceLaneThroughputResponse.model_validate(result)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session as SqlAlchemySession

    engine = create_engine("sqlite:///:memory:")
    now = datetime.now(timezone.utc)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    id INTEGER PRIMARY KEY,
                    registry_source TEXT NOT NULL,
                    first_seen TIMESTAMP,
                    last_seen TIMESTAMP,
                    last_scanned TIMESTAMP,
                    scan_count INTEGER NOT NULL DEFAULT 0,
                    risk_tier TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (registry_source, first_seen, last_seen, last_scanned, scan_count, risk_tier)
                VALUES
                    (:registry_source, :first_seen, :last_seen, :last_scanned, :scan_count, :risk_tier)
                """
            ),
            [
                {
                    "registry_source": "registry-a",
                    "first_seen": now - timedelta(days=2),
                    "last_seen": now - timedelta(hours=2),
                    "last_scanned": now - timedelta(hours=1),
                    "scan_count": 4,
                    "risk_tier": "high",
                },
                {
                    "registry_source": "registry-a",
                    "first_seen": now - timedelta(days=1),
                    "last_seen": now - timedelta(hours=1),
                    "last_scanned": now - timedelta(minutes=30),
                    "scan_count": 6,
                    "risk_tier": "medium",
                },
                {
                    "registry_source": "registry-b",
                    "first_seen": now - timedelta(days=3),
                    "last_seen": now - timedelta(hours=3),
                    "last_scanned": None,
                    "scan_count": 0,
                    "risk_tier": "low",
                },
            ],
        )

    with SqlAlchemySession(engine) as session:
        response = get_registry_source_lane_throughput(days=30, session=session)
        payload = (
            response.model_dump()
            if hasattr(response, "model_dump")
            else response.dict()
            if hasattr(response, "dict")
            else response
        )
        assert len(payload["lanes"]) == 2
        assert any(lane["first_seen"] == 2 for lane in payload["lanes"])
        assert payload["totals"]["servers"] == 3

    print("PASS")