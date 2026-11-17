"""logic.py -- the service's data/computation layer (one concern, one file)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveEvent, PerspectiveSnapshot


class TierDriftEvent(BaseModel):
    server_id: str
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: datetime


class TierDriftSeriesPoint(BaseModel):
    date: date
    changed: int
    escalated: int
    downgraded: int
    unseen: int


class TierDriftSummary(BaseModel):
    total_events: int
    unseen_events: int
    escalations: int
    downgrades: int


class TierDriftResponse(BaseModel):
    perspective_id: str
    name: str
    window_days: int
    series: List[TierDriftSeriesPoint]
    events: List[TierDriftEvent]
    summary: TierDriftSummary


class TierDriftSummaryResponse(BaseModel):
    summary: TierDriftSummary


class PerspectiveNotFoundError(Exception):
    """Raised when the requested perspective does not exist."""


def _is_escalation(change_type: object) -> bool:
    return str(change_type or "").strip().lower() == "escalated"


def _is_downgrade(change_type: object) -> bool:
    return str(change_type or "").strip().lower() == "downgraded"


def _event_dict(event: PerspectiveEvent) -> TierDriftEvent:
    return TierDriftEvent(
        server_id=str(event.server_id),
        change_type=str(event.change_type),
        old_tier=event.old_tier,
        new_tier=event.new_tier,
        seen=bool(event.seen),
        created_at=event.created_at,
    )


def _summary(events: List[PerspectiveEvent]) -> TierDriftSummary:
    return TierDriftSummary(
        total_events=len(events),
        unseen_events=sum(1 for event in events if event.seen is False),
        escalations=sum(1 for event in events if _is_escalation(event.change_type)),
        downgrades=sum(1 for event in events if _is_downgrade(event.change_type)),
    )


def _series(events: List[PerspectiveEvent]) -> List[TierDriftSeriesPoint]:
    grouped: Dict[date, Dict[str, int]] = {}

    for event in events:
        event_date = event.created_at.date()
        point = grouped.setdefault(
            event_date,
            {
                "changed": 0,
                "escalated": 0,
                "downgraded": 0,
                "unseen": 0,
            },
        )
        point["changed"] += 1
        if _is_escalation(event.change_type):
            point["escalated"] += 1
        if _is_downgrade(event.change_type):
            point["downgraded"] += 1
        if event.seen is False:
            point["unseen"] += 1

    return [
        TierDriftSeriesPoint(date=event_date, **counts)
        for event_date, counts in sorted(grouped.items())
    ]


def _window_start(window_days: int) -> datetime:
    if window_days < 1:
        raise ValueError("window_days must be at least 1")
    return datetime.now(timezone.utc) - timedelta(days=window_days)


def _legal_tiers(db: Session) -> set[str]:
    tiers = db.execute(
        select(McpServerRegistry.risk_tier).distinct()
    ).scalars().all()
    return {str(tier) for tier in tiers if tier is not None}


def get_tier_drift(
    db: Session,
    perspective_id: str,
    window_days: int = 30,
) -> TierDriftResponse:
    """Return tier changes for one perspective over the requested time window."""
    perspective = db.execute(
        select(Perspective).where(Perspective.id == perspective_id)
    ).scalar_one_or_none()
    if perspective is None:
        raise PerspectiveNotFoundError(str(perspective_id))

    cutoff = _window_start(window_days)
    events = list(
        db.execute(
            select(PerspectiveEvent)
            .join(Perspective, PerspectiveEvent.perspective_id == Perspective.id)
            .where(
                PerspectiveEvent.perspective_id == perspective_id,
                PerspectiveEvent.created_at >= cutoff,
            )
            .order_by(PerspectiveEvent.created_at, PerspectiveEvent.id)
        ).scalars().all()
    )

    snapshots = list(
        db.execute(
            select(PerspectiveSnapshot)
            .where(PerspectiveSnapshot.perspective_id == perspective_id)
            .order_by(PerspectiveSnapshot.taken_at)
        ).scalars().all()
    )
    if snapshots:
        latest_snapshot = snapshots[-1].taken_at
        if latest_snapshot is not None:
            events = [
                event
                for event in events
                if event.created_at <= latest_snapshot
            ]

    _legal_tiers(db)

    return TierDriftResponse(
        perspective_id=str(perspective.id),
        name=perspective.name,
        window_days=window_days,
        series=_series(events),
        events=[_event_dict(event) for event in events],
        summary=_summary(events),
    )


def get_tier_drift_summary(
    db: Session,
    window_days: int = 30,
) -> TierDriftSummaryResponse:
    """Return aggregate tier-drift counts across all perspectives."""
    cutoff = _window_start(window_days)
    events = list(
        db.execute(
            select(PerspectiveEvent)
            .join(Perspective, PerspectiveEvent.perspective_id == Perspective.id)
            .where(PerspectiveEvent.created_at >= cutoff)
            .order_by(PerspectiveEvent.created_at, PerspectiveEvent.id)
        ).scalars().all()
    )

    _legal_tiers(db)
    return TierDriftSummaryResponse(summary=_summary(events))


def get_perspective_tier_drift(
    db: Session,
    perspective_id: str,
    window_days: int = 30,
) -> TierDriftResponse:
    """Compatibility name for callers using the resource-oriented function name."""
    return get_tier_drift(db, perspective_id, window_days)


def get_summary(
    db: Session,
    window_days: int = 30,
) -> TierDriftSummaryResponse:
    """Compatibility name for the aggregate tier-drift summary."""
    return get_tier_drift_summary(db, window_days)


__all__ = [
    "PerspectiveNotFoundError",
    "TierDriftEvent",
    "TierDriftResponse",
    "TierDriftSeriesPoint",
    "TierDriftSummary",
    "TierDriftSummaryResponse",
    "get_perspective_tier_drift",
    "get_summary",
    "get_tier_drift",
    "get_tier_drift_summary",
]