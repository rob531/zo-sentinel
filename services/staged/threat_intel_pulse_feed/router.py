"""HTTP surface for the threat-intelligence pulse feed."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_threat_intel_pulses

router = APIRouter(prefix="/api/threatintel", tags=["threat-intel"])


class PulseResponse(BaseModel):
    pulse_id: str
    pulse_name: str
    pulse_created: str
    indicator_count: int
    aggregator: bool
    indicator_types: list[str]
    sources: list[str]
    associated_servers: int


class PulseFeedResponse(BaseModel):
    days: int
    total_pulses: int
    pulses: list[PulseResponse]
    by_indicator_type: list[dict[str, Any]]
    orphan_pulses: int


def _iso8601(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


@router.get("/pulses", response_model=PulseFeedResponse)
def get_pulses(
    days: int = Query(default=7, ge=0),
    indicator_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> PulseFeedResponse:
    result = get_threat_intel_pulses(
        db,
        days=days,
        indicator_type=indicator_type,
        limit=max(1, min(limit, 200)),
    )

    pulses = [
        PulseResponse(
            pulse_id=str(pulse["pulse_id"]),
            pulse_name=str(pulse.get("pulse_name") or ""),
            pulse_created=_iso8601(pulse["pulse_created"]),
            indicator_count=int(pulse.get("indicator_count") or 0),
            aggregator=bool(pulse.get("aggregator", False)),
            indicator_types=[str(item) for item in (pulse.get("indicator_types") or [])],
            sources=[str(item) for item in (pulse.get("sources") or [])],
            associated_servers=int(pulse.get("associated_servers") or 0),
        )
        for pulse in (result.get("pulses") or [])
    ]

    by_indicator_type = [
        {
            "indicator_type": str(item["indicator_type"]),
            "count": int(item["count"]),
        }
        for item in (result.get("by_indicator_type") or [])
    ]

    return PulseFeedResponse(
        days=int(result.get("days", days)),
        total_pulses=int(result.get("total_pulses", len(pulses))),
        pulses=pulses,
        by_indicator_type=by_indicator_type,
        orphan_pulses=int(result.get("orphan_pulses", 0)),
    )