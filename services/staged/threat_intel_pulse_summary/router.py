"""HTTP surface for the threat-intel pulse summary service."""
from __future__ import annotations

import inspect
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from . import logic as _logic
from .logic import *  # noqa: F401,F403

router = APIRouter(prefix="/api/threat-intel", tags=["threat-intel"])


class PulseSummary(BaseModel):
    pulse_id: str
    pulse_name: str | None = None
    pulse_created: str | None = None
    indicator_count: int
    aggregator_count: int
    indicator_types: list[str]
    sources: list[str]
    latest_fetched_at: str | None = None


class PulseSummaryResponse(BaseModel):
    days: int
    total_pulses: int
    pulses: list[PulseSummary]


def _summary_function():
    preferred = (
        "summarize_threat_intel_pulses",
        "summarize_pulses",
        "get_threat_intel_pulse_summary",
        "get_threat_intel_pulses",
        "get_pulse_summaries",
        "get_pulse_summary",
        "list_pulses",
    )
    available = {name: getattr(_logic, name) for name in dir(_logic)}
    for name in preferred:
        candidate = available.get(name)
        if callable(candidate):
            return candidate

    candidates = []
    for name, candidate in available.items():
        if name.startswith("_") or not callable(candidate):
            continue
        lowered = name.lower()
        if "summar" not in lowered and not (
            "pulse" in lowered and any(word in lowered for word in ("get", "list", "fetch"))
        ):
            continue
        try:
            parameters = inspect.signature(candidate).parameters
        except (TypeError, ValueError):
            continue
        parameter_names = set(parameters)
        if {"db", "session"} & parameter_names or "days" in parameter_names or "limit" in parameter_names:
            candidates.append(candidate)
    if not candidates:
        raise RuntimeError("No threat-intel pulse summary function is available in logic.py")
    return candidates[0]


def _invoke_summary(db: Session, days: int, limit: int) -> Any:
    function = _summary_function()
    parameters = inspect.signature(function).parameters
    args: list[Any] = []
    kwargs: dict[str, Any] = {}

    database_parameter = next(
        (name for name in ("db", "session", "database") if name in parameters),
        None,
    )
    if database_parameter is not None:
        kwargs[database_parameter] = db
    elif parameters and not any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL
        for parameter in parameters.values()
    ):
        args.append(db)

    days_parameter = next(
        (name for name in ("days", "window_days", "lookback_days") if name in parameters),
        None,
    )
    if days_parameter is not None:
        kwargs[days_parameter] = days

    limit_parameter = next(
        (name for name in ("limit", "max_pulses", "max_rows") if name in parameters),
        None,
    )
    if limit_parameter is not None:
        kwargs[limit_parameter] = limit

    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        kwargs.setdefault("days", days)
        kwargs.setdefault("limit", limit)

    return function(*args, **kwargs)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _normalize_pulse(pulse: Any) -> dict[str, Any]:
    if isinstance(pulse, dict):
        data = pulse
    else:
        data = {
            name: getattr(pulse, name)
            for name in (
                "pulse_id",
                "pulse_name",
                "pulse_created",
                "indicator_count",
                "aggregator_count",
                "indicator_types",
                "sources",
                "latest_fetched_at",
            )
            if hasattr(pulse, name)
        }

    indicator_types = data.get("indicator_types") or []
    sources = data.get("sources") or []
    return {
        "pulse_id": str(data.get("pulse_id") or "unassigned"),
        "pulse_name": _optional_string(data.get("pulse_name")),
        "pulse_created": _optional_string(data.get("pulse_created")),
        "indicator_count": int(data.get("indicator_count") or 0),
        "aggregator_count": int(data.get("aggregator_count") or 0),
        "indicator_types": [str(value) for value in indicator_types],
        "sources": [str(value) for value in sources],
        "latest_fetched_at": _optional_string(data.get("latest_fetched_at")),
    }


@router.get("/pulses", response_model=PulseSummaryResponse)
def get_pulses(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> PulseSummaryResponse:
    result = _invoke_summary(db, days, limit)
    if isinstance(result, dict):
        raw_pulses = result.get("pulses", result.get("items", []))
        reported_total = result.get("total_pulses")
    elif isinstance(result, tuple) and len(result) == 2:
        raw_pulses, reported_total = result
    else:
        raw_pulses = result
        reported_total = None

    pulses = [_normalize_pulse(pulse) for pulse in (raw_pulses or [])]
    total_pulses = len(pulses)
    if isinstance(reported_total, int):
        total_pulses = max(total_pulses, reported_total)

    return PulseSummaryResponse(days=days, total_pulses=total_pulses, pulses=pulses)


def _self_test() -> None:
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import ThreatIntelRef

    ThreatIntelRef.__table__.c.pulse_id.nullable = True
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    now = datetime.now(timezone.utc)

    session.add_all(
        [
            ThreatIntelRef(
                id=1,
                indicator_type="ip",
                indicator_value="192.0.2.10",
                pulse_id="pulse-alpha",
                pulse_name="Alpha",
                pulse_created=now - timedelta(days=2),
                is_aggregator=False,
                source="osint",
                source_url="https://example.test/1",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-alpha",
                pulse_name="Alpha",
                pulse_created=now - timedelta(days=2),
                is_aggregator=True,
                source="feed",
                source_url="https://example.test/2",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="hash",
                indicator_value="abc123",
                pulse_id="pulse-beta",
                pulse_name="Beta",
                pulse_created=now - timedelta(days=3),
                is_aggregator=False,
                source="osint",
                source_url="https://example.test/3",
                fetched_at=now - timedelta(hours=1),
            ),
            ThreatIntelRef(
                id=4,
                indicator_type="url",
                indicator_value="https://unassigned.test",
                pulse_id=None,
                pulse_name=None,
                pulse_created=now - timedelta(days=1),
                is_aggregator=False,
                source="manual",
                source_url="https://example.test/4",
                fetched_at=now,
            ),
        ]
    )
    session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_session():
        return session

    test_app.dependency_overrides[get_session] = override_session
    response = TestClient(test_app).get("/api/threat-intel/pulses")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_pulses"] == 3, payload
    assert payload["days"] == 30, payload
    unassigned = next(pulse for pulse in payload["pulses"] if pulse["pulse_id"] == "unassigned")
    assert unassigned["indicator_count"] == 1, payload
    assert any(pulse["aggregator_count"] == 1 for pulse in payload["pulses"]), payload
    assert all(
        isinstance(value, str)
        for pulse in payload["pulses"]
        for value in pulse["indicator_types"]
    ), payload
    print("PASS")


if __name__ == "__main__":
    _self_test()