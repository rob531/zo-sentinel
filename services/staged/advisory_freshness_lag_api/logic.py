from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel
from sqlalchemy import text

from app.db import SessionLocal


DEFAULT_BUDGET_HOURS = 168

FEED_COLUMNS = (
    "id",
    "feed",
    "summary",
    "severity",
    "ecosystem",
    "package",
    "affected_ranges",
    "aliases",
    "source_url",
    "published_at",
    "fetched_at",
    "identities",
    "content_hash",
)


class FreshnessLagFeed(BaseModel):
    feed: str
    advisory_count: int
    newest_published_at: Optional[str]
    newest_fetched_at: Optional[str]
    median_lag_hours: Optional[float]
    stale_feed: bool


class FreshnessLagData(BaseModel):
    advisory_count: int
    feeds: list[FreshnessLagFeed]


class FreshnessLagResponse(BaseModel):
    generated_at: str
    default_budget_hours: int
    overall_advisory_count: int
    feeds: list[FreshnessLagFeed]


router = APIRouter()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            return _as_utc(datetime.fromisoformat(candidate))
        except ValueError:
            for pattern in (
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
            ):
                try:
                    parsed = datetime.strptime(candidate, pattern)
                    return _as_utc(parsed)
                except ValueError:
                    continue
    return None


def _timestamp_string(value: Any) -> Optional[str]:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return None
    return parsed.isoformat()


def _budget_for_feed(
    feed: str,
    default_budget_hours: int,
    feed_budget_hours: Optional[Mapping[str, float]],
) -> float:
    if feed_budget_hours is not None and feed in feed_budget_hours:
        return float(feed_budget_hours[feed])
    return float(default_budget_hours)


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[midpoint])
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def calculate_freshness_lag(
    rows: Iterable[Mapping[str, Any]],
    default_budget_hours: int = DEFAULT_BUDGET_HOURS,
    feed_budget_hours: Optional[Mapping[str, float]] = None,
    now: Optional[datetime] = None,
) -> FreshnessLagData:
    reference_time = _as_utc(now or datetime.now(timezone.utc))
    grouped: dict[str, list[Mapping[str, Any]]] = {}

    for row in rows:
        feed = row.get("feed")
        if feed is None:
            continue
        feed_name = str(feed)
        grouped.setdefault(feed_name, []).append(row)

    feed_results: list[FreshnessLagFeed] = []

    for feed_name in sorted(grouped):
        feed_rows = grouped[feed_name]
        published_times = [
            parsed
            for parsed in (_parse_timestamp(row.get("published_at")) for row in feed_rows)
            if parsed is not None
        ]
        fetched_times = [
            parsed
            for parsed in (_parse_timestamp(row.get("fetched_at")) for row in feed_rows)
            if parsed is not None
        ]

        newest_published = max(published_times) if published_times else None
        newest_fetched = max(fetched_times) if fetched_times else None

        lags: list[float] = []
        for row in feed_rows:
            published = _parse_timestamp(row.get("published_at"))
            fetched = _parse_timestamp(row.get("fetched_at"))
            if published is not None and fetched is not None:
                lags.append((fetched - published).total_seconds() / 3600.0)

        budget_hours = _budget_for_feed(
            feed_name, default_budget_hours, feed_budget_hours
        )
        stale = False
        if newest_published is not None:
            stale = newest_published < reference_time - timedelta(hours=budget_hours)

        feed_results.append(
            FreshnessLagFeed(
                feed=feed_name,
                advisory_count=len(feed_rows),
                newest_published_at=(
                    newest_published.isoformat() if newest_published is not None else None
                ),
                newest_fetched_at=(
                    newest_fetched.isoformat() if newest_fetched is not None else None
                ),
                median_lag_hours=_median(lags),
                stale_feed=stale,
            )
        )

    return FreshnessLagData(
        advisory_count=sum(len(feed_rows) for feed_rows in grouped.values()),
        feeds=feed_results,
    )


def _fetch_feed_rows(session: Any) -> list[dict[str, Any]]:
    columns = ", ".join(FEED_COLUMNS)
    feed_sql = text(
        "SELECT DISTINCT feed "
        "FROM vuln_advisories "
        "WHERE feed IS NOT NULL "
        "ORDER BY feed"
    )
    feed_rows = session.execute(feed_sql).mappings().all()
    feeds = [str(row["feed"]) for row in feed_rows if row["feed"] is not None]

    advisory_sql = text(
        f"SELECT {columns} "
        "FROM vuln_advisories "
        "WHERE feed = :feed "
        "ORDER BY id"
    )

    rows: list[dict[str, Any]] = []
    for feed in feeds:
        result = session.execute(advisory_sql, {"feed": feed})
        rows.extend(dict(row) for row in result.mappings().all())
    return rows


def get_freshness_lag_payload(
    default_budget_hours: int = DEFAULT_BUDGET_HOURS,
    feed_budget_hours: Optional[Mapping[str, float]] = None,
    session: Any = None,
    now: Optional[datetime] = None,
) -> FreshnessLagData:
    owns_session = session is None
    active_session = session if session is not None else SessionLocal()

    try:
        rows = _fetch_feed_rows(active_session)
        return calculate_freshness_lag(
            rows=rows,
            default_budget_hours=default_budget_hours,
            feed_budget_hours=feed_budget_hours,
            now=now,
        )
    finally:
        if owns_session:
            active_session.close()


def get_freshness_lag_report(
    default_budget_hours: int = DEFAULT_BUDGET_HOURS,
    feed_budget_hours: Optional[Mapping[str, float]] = None,
    session: Any = None,
    now: Optional[datetime] = None,
) -> FreshnessLagResponse:
    generated_at = _as_utc(now or datetime.now(timezone.utc))
    payload = get_freshness_lag_payload(
        default_budget_hours=default_budget_hours,
        feed_budget_hours=feed_budget_hours,
        session=session,
        now=generated_at,
    )
    return FreshnessLagResponse(
        generated_at=generated_at.isoformat(),
        default_budget_hours=int(default_budget_hours),
        overall_advisory_count=payload.advisory_count,
        feeds=payload.feeds,
    )


def get_advisory_freshness_lag(
    default_budget_hours: int = DEFAULT_BUDGET_HOURS,
    feed_budget_hours: Optional[Mapping[str, float]] = None,
    session: Any = None,
    now: Optional[datetime] = None,
) -> FreshnessLagResponse:
    return get_freshness_lag_report(
        default_budget_hours=default_budget_hours,
        feed_budget_hours=feed_budget_hours,
        session=session,
        now=now,
    )


@router.get(
    "/advisories/freshness-lag",
    response_model=FreshnessLagResponse,
    summary="Report advisory feed freshness lag",
)
def read_advisory_freshness_lag() -> FreshnessLagResponse:
    return get_freshness_lag_report()


app = FastAPI(title="Advisory Freshness Lag API")
app.include_router(router, prefix="/api")


if __name__ == "__main__":
    reference = datetime.now(timezone.utc)
    sample_rows = [
        {
            "feed": "feed-a",
            "published_at": reference - timedelta(days=3),
            "fetched_at": reference,
        },
        {
            "feed": "feed-b",
            "published_at": reference - timedelta(days=10),
            "fetched_at": reference,
        },
    ]
    sample = calculate_freshness_lag(sample_rows, now=reference)
    assert sample.advisory_count == 2
    assert len(sample.feeds) == 2
    assert sample.feeds[0].stale_feed is False
    assert sample.feeds[1].stale_feed is True
    print("PASS")