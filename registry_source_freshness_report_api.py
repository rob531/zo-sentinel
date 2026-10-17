"""GET /registry/freshness-report: scan staleness per registry source.

Reads Fly Postgres through the injected session. Until FU-667 (#6985) this
POSTed SQL to http://127.0.0.1:8772/query -- the factory DuckDB write_service,
which runs on the ZoComputer host and is not in the Fly image -- so on Fly the
route could only 500 (Errno 111), from its 2026-09-28 mount (#5689) on.
The data was in Postgres the whole time; the handler already took a session.
"""
from datetime import datetime, timezone
from statistics import mean
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()

class SourceFreshness(BaseModel):
    source: str
    total_servers: int
    avg_days_since_last_scan: float
    max_days_since_last_scan: int
    stale_servers_pct: float
    oldest_server_seen_at: str

class FreshnessReport(BaseModel):
    sources: List[SourceFreshness]
    overall_stale_pct: float
    alerts: List[str]

def get_stale_threshold_days(stale_threshold_days: Optional[int] = Query(30)) -> int:
    return stale_threshold_days

def _as_utc(ts: datetime) -> datetime:
    # timestamptz comes back aware from Postgres, naive from sqlite.
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)

def calculate_days_since_last_scan(last_scanned: datetime) -> int:
    return (datetime.now(timezone.utc) - _as_utc(last_scanned)).days

def get_registry_data(session: Session) -> List[tuple]:
    """(registry_source, last_scanned) for every scanned server. Two columns, not
    SELECT *. Never-scanned servers have no scan age; /registry/freshness counts them."""
    return session.execute(
        select(McpServerRegistry.registry_source, McpServerRegistry.last_scanned)
        .where(McpServerRegistry.last_scanned.isnot(None))
    ).all()

@router.get("/registry/freshness-report", response_model=FreshnessReport)
def get_freshness_report(
    stale_threshold_days: int = Depends(get_stale_threshold_days),
    session: Session = Depends(get_session)
):
    registry_data = get_registry_data(session)

    sources = {}
    total_servers = 0
    total_stale_servers = 0

    for source, last_scanned in registry_data:
        source = source or "unknown"
        last_scanned = _as_utc(last_scanned)

        days_since_last_scan = calculate_days_since_last_scan(last_scanned)
        is_stale = days_since_last_scan > stale_threshold_days

        if source not in sources:
            sources[source] = {
                "total_servers": 0,
                "days_since_last_scan": [],
                "stale_servers": 0,
                "oldest_server_seen_at": last_scanned
            }

        sources[source]["total_servers"] += 1
        sources[source]["days_since_last_scan"].append(days_since_last_scan)
        if is_stale:
            sources[source]["stale_servers"] += 1
        if last_scanned < sources[source]["oldest_server_seen_at"]:
            sources[source]["oldest_server_seen_at"] = last_scanned

        total_servers += 1
        if is_stale:
            total_stale_servers += 1

    report_sources = []
    alerts = []

    for source, data in sources.items():
        avg_days = mean(data["days_since_last_scan"])
        max_days = max(data["days_since_last_scan"])
        stale_pct = (data["stale_servers"] / data["total_servers"]) * 100

        report_sources.append(
            SourceFreshness(
                source=source,
                total_servers=data["total_servers"],
                avg_days_since_last_scan=avg_days,
                max_days_since_last_scan=max_days,
                stale_servers_pct=stale_pct,
                oldest_server_seen_at=data["oldest_server_seen_at"].isoformat()
            )
        )

        if stale_pct > 0:
            alerts.append(
                f"Source '{source}' has {data['stale_servers']} stale servers "
                f"({stale_pct:.1f}%) - oldest scan was {data['oldest_server_seen_at'].isoformat()}"
            )

    overall_stale_pct = (total_stale_servers / total_servers) * 100 if total_servers > 0 else 0

    return FreshnessReport(
        sources=report_sources,
        overall_stale_pct=overall_stale_pct,
        alerts=alerts
    )
