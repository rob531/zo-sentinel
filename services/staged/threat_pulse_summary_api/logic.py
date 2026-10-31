from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

router = APIRouter()

FRESHNESS_WINDOW_HOURS = 24

_FEED_FIELDS = (
    "source",
    "feed",
    "feed_name",
    "source_name",
    "provider",
    "origin",
    "channel",
    "ref_type",
    "kind",
)
_TIMESTAMP_FIELDS = (
    "last_pulse_at",
    "last_seen_at",
    "last_seen",
    "observed_at",
    "retrieved_at",
    "fetched_at",
    "pulse_at",
    "published_at",
    "updated_at",
    "created_at",
    "timestamp",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _feed_name(ref: Any) -> str:
    for field in _FEED_FIELDS:
        value = getattr(ref, field, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "unknown"


def _pulse_timestamp(ref: Any) -> datetime | None:
    for field in _TIMESTAMP_FIELDS:
        value = _as_utc(getattr(ref, field, None))
        if value is not None:
            return value

    advisory = getattr(ref, "advisory", None)
    if advisory is None:
        advisory = getattr(ref, "vuln_advisory", None)
    if advisory is not None:
        for field in _TIMESTAMP_FIELDS:
            value = _as_utc(getattr(advisory, field, None))
            if value is not None:
                return value
    return None


def summarize_threat_pulses(session: Session) -> dict[str, Any]:
    refs = session.scalars(select(ThreatIntelRef)).all()
    grouped: dict[str, list[datetime]] = {}

    for ref in refs:
        feed = _feed_name(ref)
        timestamps = grouped.setdefault(feed, [])
        timestamp = _pulse_timestamp(ref)
        if timestamp is not None:
            timestamps.append(timestamp)

    now = utc_now()
    feeds: list[dict[str, Any]] = []

    for feed in sorted(grouped):
        timestamps = grouped[feed]
        latest = max(timestamps) if timestamps else None
        age_hours = None if latest is None else max(0.0, (now - latest).total_seconds() / 3600)
        fresh = age_hours is not None and age_hours <= FRESHNESS_WINDOW_HOURS

        feeds.append(
            {
                "feed": feed,
                "pulse_count": len(grouped[feed]),
                "latest_pulse_at": _iso(latest),
                "age_hours": None if age_hours is None else round(age_hours, 2),
                "fresh": fresh,
                "status": "fresh" if fresh else "stale" if latest is not None else "unknown",
            }
        )

    return {
        "total_pulses": len(refs),
        "populated_feeds": len(feeds),
        "fresh_feeds": sum(1 for feed in feeds if feed["fresh"]),
        "stale_feeds": sum(
            1 for feed in feeds if feed["status"] == "stale"
        ),
        "freshness_window_hours": FRESHNESS_WINDOW_HOURS,
        "feeds": feeds,
    }


@router.get("/")
def get_threat_pulse_summary(session: Session = Depends(get_session)) -> dict[str, Any]:
    return summarize_threat_pulses(session)


get_pulse_summary = get_threat_pulse_summary


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    class _EmptyResult:
        def all(self) -> list[Any]:
            return []

    class _SelfTestSession:
        def scalars(self, statement: Any) -> _EmptyResult:
            return _EmptyResult()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: _SelfTestSession()

    response = TestClient(that_app).get("/")
    assert response.status_code == 200
    assert response.json()["total_pulses"] == 0
    print("PASS")