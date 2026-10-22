from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import compute_timeline

router = APIRouter(prefix="/registry/verdict-timeline", tags=["registry"])


class TimelineEntry(BaseModel):
    date: str
    verdict: Optional[str]
    risk_tier: Optional[str]
    count: int


class TimelineResponse(BaseModel):
    days: int
    source: Optional[str]
    series: List[TimelineEntry]
    tier_totals: Dict[str, int]
    unassessed: int


class _TimelineRow(dict):
    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _row_value(row, name, default=None):
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _build_timeline(rows, days: int, source: Optional[str]) -> Dict:
    buckets = {}
    tier_totals = {}
    unassessed = 0

    for row in rows:
        assessed = _row_value(row, "last_assessed")
        verdict = _row_value(row, "verdict")
        risk_tier = _row_value(row, "risk_tier")

        if assessed is None:
            unassessed += 1
            continue

        assessed_date = assessed.date() if hasattr(assessed, "date") else assessed
        key = (str(assessed_date), verdict)
        if key not in buckets:
            buckets[key] = {
                "date": str(assessed_date),
                "verdict": verdict,
                "risk_tier": risk_tier,
                "count": 0,
            }
        buckets[key]["count"] += 1

        if risk_tier is not None:
            tier_totals[str(risk_tier)] = tier_totals.get(str(risk_tier), 0) + 1

    series = sorted(
        buckets.values(),
        key=lambda item: (item["date"], item["verdict"] or ""),
    )
    return {
        "days": days,
        "source": source,
        "series": series,
        "tier_totals": tier_totals,
        "unassessed": unassessed,
    }


@router.get("", response_model=TimelineResponse)
def get_registry_verdict_timeline(
    days: int = Query(30),
    source: Optional[str] = Query(None),
    verdict: Optional[str] = Query(None),
    session: Session = Depends(get_session),
) -> Dict:
    days = max(1, min(365, days))

    query = select(
        McpServerRegistry.server_id,
        McpServerRegistry.verdict,
        McpServerRegistry.risk_tier,
        McpServerRegistry.registry_source,
        McpServerRegistry.last_assessed,
        McpServerRegistry.first_seen,
        McpServerRegistry.last_seen,
    )
    if source is not None:
        query = query.where(McpServerRegistry.registry_source == source)
    if verdict is not None:
        query = query.where(McpServerRegistry.verdict == verdict)

    result = session.execute(query).all()
    rows = []
    for row in result:
        rows.append(
            _TimelineRow(
                server_id=row.server_id,
                verdict=row.verdict,
                risk_tier=row.risk_tier,
                registry_source=row.registry_source,
                last_assessed=row.last_assessed,
                first_seen=row.first_seen,
                last_seen=row.last_seen,
            )
        )

    try:
        timeline = compute_timeline(rows)
        if not isinstance(timeline, dict) or "series" not in timeline:
            raise ValueError("invalid timeline result")
    except (AttributeError, KeyError, TypeError, ValueError):
        timeline = _build_timeline(rows, days, source)

    timeline["days"] = days
    timeline["source"] = source
    timeline.setdefault("series", [])
    timeline.setdefault("tier_totals", {})
    timeline.setdefault("unassessed", 0)

    for entry in timeline["series"]:
        if hasattr(entry, "date") and not isinstance(entry, dict):
            entry = entry.model_dump() if hasattr(entry, "model_dump") else entry.dict()
        if isinstance(entry, dict):
            assessed_date = entry.get("date")
            if assessed_date is not None and not isinstance(assessed_date, str):
                assessed_date = str(assessed_date)
            entry["date"] = assessed_date

    return timeline