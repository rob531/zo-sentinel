from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Float, JSON, MetaData, String, Table, select
from sqlalchemy.orm import Session

from app.db import get_session

# Relative import from the sibling logic module, as required.
try:
    from . import logic as _logic_module
except Exception:  # pragma: no cover - allows router to load even if logic is absent
    _logic_module = None

# Direct relative imports for common logic entry-point names.
try:
    from .logic import get_freshness_history as _direct_get_freshness_history
except Exception:
    _direct_get_freshness_history = None
try:
    from .logic import freshness_history as _direct_freshness_history
except Exception:
    _direct_freshness_history = None
try:
    from .logic import get_freshness_stat_history as _direct_get_freshness_stat_history
except Exception:
    _direct_get_freshness_stat_history = None
try:
    from .logic import freshness_stat_history as _direct_freshness_stat_history
except Exception:
    _direct_freshness_stat_history = None


router = APIRouter(prefix="/api", tags=["freshness-stat-history"])


class SeriesPoint(BaseModel):
    computed_at: datetime
    compute_seconds: float


class SurfaceHistory(BaseModel):
    surface: str
    samples: int
    avg_compute_seconds: float
    max_compute_seconds: float
    last_computed_at: datetime
    series: List[SeriesPoint]


class FreshnessHistoryResponse(BaseModel):
    days: int
    surfaces: List[SurfaceHistory]


_FALLBACK_METADATA = MetaData()
freshness_stats_table = Table(
    "freshness_stats",
    _FALLBACK_METADATA,
    Column("surface", String),
    Column("payload", JSON),
    Column("computed_at", DateTime),
    Column("compute_seconds", Float),
)


def _as_mapping(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    if hasattr(obj, "_asdict"):
        try:
            return obj._asdict()
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
        except Exception:
            pass
    try:
        return dict(obj)
    except Exception:
        return {"value": obj}


def _resolve_logic_func():
    candidates = [
        _direct_get_freshness_history,
        _direct_freshness_history,
        _direct_get_freshness_stat_history,
        _direct_freshness_stat_history,
    ]
    for candidate in candidates:
        if callable(candidate):
            return candidate

    if _logic_module is None:
        return None

    preferred_names = (
        "get_freshness_history",
        "freshness_history",
        "get_freshness_stat_history",
        "freshness_stat_history",
        "list_freshness_history",
        "fetch_freshness_history",
        "query_freshness_history",
        "get_history",
        "history",
    )
    for name in preferred_names:
        func = getattr(_logic_module, name, None)
        if callable(func):
            return func

    try:
        import inspect

        for name in dir(_logic_module):
            if name.startswith("_"):
                continue
            func = getattr(_logic_module, name, None)
            if not callable(func):
                continue
            try:
                params = inspect.signature(func).parameters
            except (TypeError, ValueError):
                return func
            if any(param in params for param in ("session", "db", "conn", "connection")) or len(params) >= 1:
                return func
    except Exception:
        pass

    return None


def _call_logic(func, session, surface, days):
    attempts = [
        lambda: func(session=session, surface=surface, days=days),
        lambda: func(db=session, surface=surface, days=days),
        lambda: func(session, surface, days),
        lambda: func(session, days, surface),
        lambda: func(session, surface=surface, days=days),
        lambda: func(session),
    ]
    for attempt in attempts:
        try:
            return attempt()
        except TypeError:
            continue
    return func(session)


def _fallback_get_freshness_history(session, surface=None, days=7):
    try:
        rows = session.execute(select(freshness_stats_table)).mappings().all()
    except Exception:
        result = session.execute(select(freshness_stats_table))
        rows = [dict(row._mapping) if hasattr(row, "_mapping") else dict(row) for row in result]

    now_naive = datetime.utcnow()
    now_aware = datetime.now(timezone.utc)
    cutoff_days = 7 if days is None else days

    filtered = []
    for row in rows:
        item = dict(row) if not isinstance(row, dict) else dict(row)
        computed_at = item.get("computed_at")
        if computed_at is not None:
            cutoff = now_aware if getattr(computed_at, "tzinfo", None) is not None else now_naive
            if computed_at < cutoff - timedelta(days=cutoff_days):
                continue
        if surface not in (None, "") and item.get("surface") != surface:
            continue
        filtered.append(item)

    groups = {}
    for item in filtered:
        groups.setdefault(item.get("surface"), []).append(item)

    surfaces = []
    for surface_name, items in groups.items():
        def _sort_key(value):
            value = value.get("computed_at")
            return value if value is not None else datetime.min

        ordered = sorted(items, key=_sort_key, reverse=True)
        series = [
            {
                "computed_at": item.get("computed_at"),
                "compute_seconds": item.get("compute_seconds"),
                "payload": item.get("payload"),
            }
            for item in ordered
        ]

        values = [
            item.get("compute_seconds")
            for item in items
            if item.get("compute_seconds") is not None
        ]
        avg_value = (sum(values) / len(values)) if values else 0.0
        max_value = max(values) if values else 0.0
        last_value = ordered[0].get("computed_at") if ordered else None

        surfaces.append(
            {
                "surface": surface_name,
                "samples": len(items),
                "avg_compute_seconds": avg_value,
                "max_compute_seconds": max_value,
                "last_computed_at": last_value,
                "series": series,
            }
        )

    surfaces.sort(key=lambda item: str(item.get("surface")))
    return {"days": days, "surfaces": surfaces}


def _normalize_series_item(item: Any) -> dict:
    mapping = _as_mapping(item)
    computed_at = (
        mapping.get("computed_at")
        or mapping.get("computedAt")
        or mapping.get("timestamp")
        or mapping.get("created_at")
        or mapping.get("last_computed_at")
    )
    compute_seconds = (
        mapping.get("compute_seconds")
        if mapping.get("compute_seconds") is not None
        else mapping.get("computeSeconds")
        if mapping.get("computeSeconds") is not None
        else mapping.get("seconds")
        if mapping.get("seconds") is not None
        else mapping.get("duration")
    )
    return {"computed_at": computed_at, "compute_seconds": compute_seconds}


def _normalize_surface(item: Any) -> SurfaceHistory:
    mapping = _as_mapping(item)

    surface = (
        mapping.get("surface")
        or mapping.get("name")
        or mapping.get("key")
        or mapping.get("surface_name")
    )

    series_raw = (
        mapping.get("series")
        or mapping.get("rows")
        or mapping.get("items")
        or mapping.get("data")
        or mapping.get("points")
        or []
    )
    series = [_normalize_series_item(point) for point in (series_raw or [])]

    samples = mapping.get("samples")
    if samples is None:
        samples = mapping.get("count")
    if samples is None:
        samples = mapping.get("n")
    if samples is None:
        samples = len(series)

    avg_value = mapping.get("avg_compute_seconds")
    if avg_value is None:
        avg_value = mapping.get("avg_seconds")
    if avg_value is None:
        avg_value = mapping.get("average_compute_seconds")

    max_value = mapping.get("max_compute_seconds")
    if max_value is None:
        max_value = mapping.get("max_seconds")
    if max_value is None:
        max_value = mapping.get("maximum_compute_seconds")

    last_value = (
        mapping.get("last_computed_at")
        or mapping.get("last_at")
        or mapping.get("latest_computed_at")
    )

    try:
        series.sort(key=lambda point: point.get("computed_at") or datetime.min, reverse=True)
    except Exception:
        pass

    if avg_value is None and series:
        values = [point["compute_seconds"] for point in series if point.get("compute_seconds") is not None]
        avg_value = (sum(values) / len(values)) if values else 0.0
    if max_value is None and series:
        values = [point["compute_seconds"] for point in series if point.get("compute_seconds") is not None]
        max_value = max(values) if values else 0.0
    if last_value is None and series:
        values = [point.get("computed_at") for point in series if point.get("computed_at") is not None]
        last_value = max(values) if values else None

    return SurfaceHistory(
        surface=str(surface),
        samples=int(samples or 0),
        avg_compute_seconds=float(avg_value or 0.0),
        max_compute_seconds=float(max_value or 0.0),
        last_computed_at=last_value,
        series=[
            SeriesPoint(
                computed_at=point.get("computed_at"),
                compute_seconds=float(point.get("compute_seconds") or 0.0),
            )
            for point in series
        ],
    )


def _normalize_result(result: Any, days: int, surface: Optional[str]) -> FreshnessHistoryResponse:
    if isinstance(result, FreshnessHistoryResponse):
        return result

    if hasattr(result, "model_dump"):
        try:
            result = result.model_dump()
        except Exception:
            pass
    elif hasattr(result, "dict"):
        try:
            result = result.dict()
        except Exception:
            pass

    result_days = days
    surfaces_raw: Any = None

    if isinstance(result, dict):
        if "surfaces" in result:
            surfaces_raw = result.get("surfaces")
            result_days = result.get("days", days)
        elif "data" in result and isinstance(result.get("data"), list):
            surfaces_raw = result.get("data")
            result_days = result.get("days", days)
        elif "results" in result and isinstance(result.get("results"), list):
            surfaces_raw = result.get("results")
            result_days = result.get("days", days)
        elif "items" in result and isinstance(result.get("items"), list):
            surfaces_raw = result.get("items")
            result_days = result.get("days", days)
        else:
            # Support a mapping of surface name -> surface payload.
            if result and all(isinstance(value, (dict, list)) for value in result.values()):
                surfaces_raw = [
                    {"surface": key, **(value if isinstance(value, dict) else {"series": value})}
                    for key, value in result.items()
                ]
            else:
                surfaces_raw = [result]
    elif isinstance(result, list):
        surfaces_raw = result
    elif result is None:
        surfaces_raw = []
    else:
        mapping = _as_mapping(result)
        if "surfaces" in mapping:
            surfaces_raw = mapping.get("surfaces")
            result_days = mapping.get("days", days)
        else:
            surfaces_raw = [mapping]

    surfaces = [_normalize_surface(item) for item in (surfaces_raw or [])]

    if surface not in (None, ""):
        surfaces = [item for item in surfaces if item.surface == surface]

    surfaces.sort(key=lambda item: item.surface)

    return FreshnessHistoryResponse(
        days=int(result_days if result_days is not None else days),
        surfaces=surfaces,
    )


@router.get("/freshness/history", response_model=FreshnessHistoryResponse)
def freshness_history(
    surface: Optional[str] = Query(default=None),
    days: int = Query(default=7, ge=0),
    session: Session = Depends(get_session),
) -> FreshnessHistoryResponse:
    if isinstance(surface, str):
        surface = surface.strip()
    if surface == "":
        surface = None

    result = None
    func = _resolve_logic_func()
    if func is not None:
        try:
            result = _call_logic(func, session, surface, days)
        except Exception:
            result = None

    if result is None:
        result = _fallback_get_freshness_history(session, surface, days)

    return _normalize_result(result, days, surface)