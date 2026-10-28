from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import *
except ImportError:
    from services.staged.threat_assoc_recency_decile_api.logic import *


router = APIRouter(prefix="/api", tags=["threat-recency-decile"])


class SeverityCounts(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class RecencyDecile(BaseModel):
    decile: int
    row_count: int
    severity_counts: SeverityCounts = Field(default_factory=SeverityCounts)


class RecencyDecileResponse(BaseModel):
    days: int
    deciles: List[RecencyDecile]


_LOGIC_FUNCTION_NAMES = (
    "get_recency_deciles",
    "get_recency_decile_data",
    "get_recency_decile_distribution",
    "calculate_recency_deciles",
    "compute_recency_deciles",
    "build_recency_deciles",
    "get_threat_recency_deciles",
)


def _find_logic_function() -> Optional[Callable[..., Any]]:
    for name in _LOGIC_FUNCTION_NAMES:
        candidate = globals().get(name)
        if callable(candidate):
            return candidate
    return None


def _call_logic(session: Session, days: int) -> Any:
    function = _find_logic_function()
    if function is None:
        raise LookupError("No recency-decile data function is exported by .logic")

    signature = inspect.signature(function)
    kwargs: Dict[str, Any] = {}

    for parameter_name in ("session", "db", "db_session"):
        if parameter_name in signature.parameters:
            kwargs[parameter_name] = session
            break

    for parameter_name in ("days", "window_days", "lookback_days"):
        if parameter_name in signature.parameters:
            kwargs[parameter_name] = days
            break

    return function(**kwargs)


def _normalize_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    raise TypeError(f"Unsupported last_seen value: {value!r}")


def _empty_deciles() -> List[Dict[str, Any]]:
    return [
        {
            "decile": decile,
            "row_count": 0,
            "severity_counts": {
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
            },
        }
        for decile in range(1, 11)
    ]


def _normalize_result(result: Any, days: int) -> List[Dict[str, Any]]:
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    elif hasattr(result, "dict"):
        result = result.dict()

    if isinstance(result, dict):
        result = result.get("deciles", [])

    if not isinstance(result, list):
        raise TypeError("The recency-decile data layer returned an unsupported result")

    deciles = _empty_deciles()
    by_decile = {entry["decile"]: entry for entry in deciles}

    for item in result:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        elif hasattr(item, "dict"):
            item = item.dict()

        if not isinstance(item, dict):
            continue

        decile = int(item.get("decile", 0))
        if decile < 1 or decile > 10:
            continue

        severity_counts = item.get("severity_counts") or {}
        if not isinstance(severity_counts, dict):
            severity_counts = {}

        normalized_counts = {
            severity: int(severity_counts.get(severity, 0) or 0)
            for severity in ("critical", "high", "medium", "low")
        }

        by_decile[decile]["row_count"] = int(item.get("row_count", 0) or 0)
        by_decile[decile]["severity_counts"] = normalized_counts

    return deciles


def _calculate_deciles(session: Session, days: int) -> List[Dict[str, Any]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)
    rows = session.execute(
        text(
            """
            SELECT severity, last_seen
            FROM mcp_threat_associations
            WHERE last_seen >= :cutoff
            """
        ),
        {"cutoff": cutoff},
    ).fetchall()

    now = datetime.now(timezone.utc)
    buckets = _empty_deciles()

    for row in rows:
        severity = str(row.severity or "unknown").strip().lower()
        if severity not in ("critical", "high", "medium", "low"):
            continue

        last_seen = _normalize_timestamp(row.last_seen)
        age_seconds = max(0.0, (now - last_seen).total_seconds())
        age_days = age_seconds / 86400.0
        decile = min(10, int((age_days * 10.0) / days) + 1)

        bucket = buckets[decile - 1]
        bucket["row_count"] += 1
        bucket["severity_counts"][severity] += 1

    return buckets


@router.get(
    "/threat/recency-decile",
    response_model=RecencyDecileResponse,
    summary="Threat-association recency decile distribution",
)
async def get_threat_recency_decile(
    days: int = Query(default=30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> RecencyDecileResponse:
    try:
        result = _call_logic(session, days)
    except LookupError:
        result = _calculate_deciles(session, days)

    deciles = _normalize_result(result, days)
    return RecencyDecileResponse(days=days, deciles=deciles)


def _run_self_test() -> None:
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_threat_associations (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    association_type TEXT,
                    severity TEXT,
                    confidence REAL,
                    first_seen DATETIME,
                    last_seen DATETIME,
                    source TEXT
                )
                """
            )
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        seeded_rows = [
            ("critical", 0.5),
            ("critical", 0.5),
            ("critical", 0.5),
            ("critical", 0.5),
            ("high", 5.0),
            ("high", 5.0),
            ("high", 5.0),
            ("medium", 20.0),
            ("medium", 20.0),
            ("low", 39.0),
        ]

        for index, (severity, age_days) in enumerate(seeded_rows, start=1):
            observed_at = now - timedelta(days=age_days)
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_threat_associations (
                        id,
                        server_id,
                        association_type,
                        severity,
                        confidence,
                        first_seen,
                        last_seen,
                        source
                    ) VALUES (
                        :id,
                        :server_id,
                        :association_type,
                        :severity,
                        :confidence,
                        :first_seen,
                        :last_seen,
                        :source
                    )
                    """
                ),
                {
                    "id": index,
                    "server_id": f"server-{index}",
                    "association_type": "cve",
                    "severity": severity,
                    "confidence": 0.9,
                    "first_seen": observed_at,
                    "last_seen": observed_at,
                    "source": "self-test",
                },
            )

    test_session = TestingSession()
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_session] = lambda: test_session

    try:
        with TestClient(application) as client:
            response = client.get(
                "/api/threat/recency-decile",
                params={"days": 40},
            )
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["days"] == 40
            assert len(payload["deciles"]) == 10
            assert sum(item["row_count"] for item in payload["deciles"]) == 10

            first_decile = payload["deciles"][0]
            assert first_decile["decile"] == 1
            assert first_decile["row_count"] == 4
            assert first_decile["severity_counts"]["critical"] == 4
    finally:
        test_session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()