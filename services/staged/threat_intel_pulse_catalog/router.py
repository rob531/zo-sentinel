"""router.py -- threat intelligence pulse catalogue HTTP surface."""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_pulse, list_pulses

router = APIRouter(prefix="/api", tags=["threat-intel"])


class PulseSummary(BaseModel):
    pulse_id: str
    pulse_name: str | None = None
    source: str | None = None
    indicator_count: int
    indicator_types: list[str] = []
    is_aggregator: bool
    oldest_fetched_at: datetime | None = None
    newest_fetched_at: datetime | None = None
    staleness_days: float | None = None


class PulseTotals(BaseModel):
    pulses: int
    indicators: int


class PulseCatalogResponse(BaseModel):
    generated_at: datetime
    as_of: datetime
    pulses: list[PulseSummary]
    totals: PulseTotals


class PulseIndicator(BaseModel):
    indicator_type: str
    indicator_value: str
    source: str | None = None
    fetched_at: datetime | None = None


class PulseDetailResponse(BaseModel):
    pulse_id: str
    pulse_name: str | None = None
    indicators: list[PulseIndicator]


def _as_of_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(normalized), datetime.min.time())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="as_of must be an ISO date or datetime") from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@router.get("/threatintel/pulses", response_model=PulseCatalogResponse)
def get_pulse_catalog(
    db: Session = Depends(get_session),
    as_of: str | None = Query(default=None),
) -> PulseCatalogResponse:
    effective_as_of = _as_of_datetime(as_of)
    result = list_pulses(db, effective_as_of)

    if isinstance(result, dict):
        raw_pulses = result.get("pulses", [])
        raw_totals = result.get("totals")
    else:
        raw_pulses = result
        raw_totals = None

    pulses = [PulseSummary.model_validate(pulse) for pulse in raw_pulses]
    totals = PulseTotals(
        pulses=len(pulses),
        indicators=sum(pulse.indicator_count for pulse in pulses),
    )
    if isinstance(raw_totals, dict):
        totals = PulseTotals.model_validate(raw_totals)

    return PulseCatalogResponse(
        generated_at=datetime.now(timezone.utc),
        as_of=effective_as_of,
        pulses=pulses,
        totals=totals,
    )


@router.get("/threatintel/pulses/{pulse_id}", response_model=PulseDetailResponse)
def get_pulse_detail(
    pulse_id: str,
    db: Session = Depends(get_session),
) -> PulseDetailResponse:
    result = get_pulse(db, pulse_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Pulse not found")
    return PulseDetailResponse.model_validate(result)