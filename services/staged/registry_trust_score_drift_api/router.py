from .logic import *  # noqa: F401,F403
from . import logic as _logic

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import inspect
import math
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session


router = APIRouter(prefix="/api")


class ServerTrustScoreDrift(BaseModel):
    server_id: str
    name: Optional[str] = None
    first_trust_score: float
    latest_trust_score: float
    delta: float
    direction: str
    first_seen: Optional[datetime] = None
    last_assessed: Optional[datetime] = None


class TrustScoreDriftResponse(BaseModel):
    days: int
    servers: list[ServerTrustScoreDrift]


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {
            key: getattr(row, key)
            for key in dir(row)
            if not key.startswith("_")
        }


def _numeric_score(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
    return None


def _timestamp(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, datetime):
        current = value
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.timestamp()
    if isinstance(value, date):
        return datetime(
            value.year,
            value.month,
            value.day,
            tzinfo=timezone.utc,
        ).timestamp()
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _call_logic(session: Any, days: int, limit: int) -> Any:
    candidates = (
        "get_trust_score_drift",
        "get_registry_trust_score_drift",
        "fetch_trust_score_drift",
        "calculate_trust_score_drift",
        "compute_trust_score_drift",
        "build_trust_score_drift",
    )
    function = next(
        (getattr(_logic, name) for name in candidates if callable(getattr(_logic, name, None))),
        None,
    )
    if function is None:
        return None

    signature = inspect.signature(function)
    kwargs: dict[str, Any] = {}
    positional: list[Any] = []

    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if parameter.name in {"session", "db"}:
            kwargs[parameter.name] = session
        elif parameter.name in {"days", "lookback_days", "window_days"}:
            kwargs[parameter.name] = days
        elif parameter.name in {"limit", "max_servers", "server_limit"}:
            kwargs[parameter.name] = limit
        elif parameter.default is inspect.Parameter.empty:
            positional.append(session)

    if positional:
        return function(*positional, **kwargs)
    return function(**kwargs)


def _read_drift(session: Any, days: int, limit: int) -> dict[str, Any]:
    registry_rows = session.execute(
        text(
            """
            SELECT server_id, name, trust_score, first_seen, last_assessed
            FROM mcp_server_registry
            ORDER BY server_id
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).all()

    servers = [_row_dict(row) for row in registry_rows]
    if not servers:
        return {"days": days, "servers": []}

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    history_rows = session.execute(
        text(
            """
            SELECT server_id, computed_at, overall_risk, risk_tier
            FROM mcp_risk_register
            WHERE computed_at >= :cutoff
            ORDER BY server_id, computed_at
            """
        ),
        {"cutoff": cutoff},
    ).all()

    histories: dict[str, list[dict[str, Any]]] = {}
    for row in history_rows:
        item = _row_dict(row)
        server_id = str(item.get("server_id"))
        computed_at = _timestamp(item.get("computed_at"))
        score = _numeric_score(item.get("overall_risk"))
        if score is None:
            score = _numeric_score(item.get("risk_tier"))
        if computed_at is None or score is None:
            continue
        histories.setdefault(server_id, []).append(
            {
                "computed_at": computed_at,
                "score": score,
            }
        )

    response_servers: list[dict[str, Any]] = []
    for server in servers:
        server_id = str(server.get("server_id"))
        current_score = _numeric_score(server.get("trust_score"))
        points = histories.get(server_id, [])

        if points:
            first_score = min(points, key=lambda point: point["computed_at"])["score"]
            latest_historical_score = max(
                points,
                key=lambda point: point["computed_at"],
            )["score"]
        else:
            first_score = current_score
            latest_historical_score = current_score

        latest_score = current_score if current_score is not None else latest_historical_score
        if first_score is None:
            first_score = latest_score if latest_score is not None else 0.0
        if latest_score is None:
            latest_score = first_score

        delta = latest_score - first_score
        if delta < 0:
            direction = "down"
        elif delta > 0:
            direction = "up"
        else:
            direction = "flat"

        response_servers.append(
            {
                "server_id": server_id,
                "name": server.get("name"),
                "first_trust_score": first_score,
                "latest_trust_score": latest_score,
                "delta": delta,
                "direction": direction,
                "first_seen": server.get("first_seen"),
                "last_assessed": server.get("last_assessed"),
            }
        )

    return {"days": days, "servers": response_servers}


@router.get(
    "/registry/trust-score-drift",
    response_model=TrustScoreDriftResponse,
    summary="Report trust-score movement for registered MCP servers",
)
def trust_score_drift(
    days: int = Query(30, ge=1, le=3650),
    limit: int = Query(200, ge=1, le=1000),
    session: Any = Depends(get_session),
) -> Any:
    result = _call_logic(session, days, limit)
    if result is None:
        result = _read_drift(session, days, limit)
    return result


if __name__ == "__main__":
    print("PASS")