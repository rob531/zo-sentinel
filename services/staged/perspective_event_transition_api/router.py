from __future__ import annotations

import inspect
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_perspective_event_transitions
except ImportError:
    try:
        from .logic import get_event_transition_stats as get_perspective_event_transitions
    except ImportError:
        try:
            from .logic import get_transition_stats as get_perspective_event_transitions
        except ImportError:
            from .logic import get_perspective_event_transition_data as get_perspective_event_transitions


router = APIRouter(prefix="/api")


class ChangeTypeCount(BaseModel):
    change_type: str
    count: int


class TierTransitionCount(BaseModel):
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    count: int


class TopServerCount(BaseModel):
    server_id: str
    count: int


class PerspectiveEventTransitionsResponse(BaseModel):
    days: int
    events: int
    by_change_type: list[ChangeTypeCount] = Field(default_factory=list)
    transitions: list[TierTransitionCount] = Field(default_factory=list)
    unseen: int
    top_servers: list[TopServerCount] = Field(default_factory=list)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "_asdict"):
        return value._asdict()
    return {}


def _first(data: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return default


def _normalise_result(
    result: Any,
    *,
    perspective_id: Optional[str],
    days: int,
) -> PerspectiveEventTransitionsResponse:
    data = _as_dict(result)

    raw_change_types = _first(data, "by_change_type", "change_types", default=[]) or []
    by_change_type = [
        ChangeTypeCount(
            change_type=str(_first(item, "change_type", "type", default="")),
            count=int(_first(item, "count", "value", default=0) or 0),
        )
        for item in raw_change_types
    ]

    raw_transitions = _first(data, "transitions", "transition_matrix", default=[]) or []
    transitions = [
        TierTransitionCount(
            old_tier=_first(item, "old_tier", "from_tier"),
            new_tier=_first(item, "new_tier", "to_tier"),
            count=int(_first(item, "count", "value", default=0) or 0),
        )
        for item in raw_transitions
    ]

    raw_top_servers = _first(data, "top_servers", "servers", default=[]) or []
    top_servers = [
        TopServerCount(
            server_id=str(_first(item, "server_id", "server", default="")),
            count=int(_first(item, "count", "value", default=0) or 0),
        )
        for item in raw_top_servers
    ]

    return PerspectiveEventTransitionsResponse(
        days=int(_first(data, "days", default=days) or days),
        events=int(_first(data, "events", "total_events", "event_count", default=0) or 0),
        by_change_type=by_change_type,
        transitions=transitions,
        unseen=int(_first(data, "unseen", "unseen_count", default=0) or 0),
        top_servers=top_servers,
    )


async def _run_logic(
    session: Session,
    perspective_id: Optional[str],
    days: int,
) -> Any:
    function = get_perspective_event_transitions
    signature = inspect.signature(function)
    parameters = signature.parameters

    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        result = function(session=session, perspective_id=perspective_id, days=days)
    else:
        kwargs: dict[str, Any] = {}
        for name, value in (
            ("session", session),
            ("db", session),
            ("perspective_id", perspective_id),
            ("days", days),
        ):
            if name in parameters:
                kwargs[name] = value

        positional = [
            parameter
            for parameter in parameters.values()
            if parameter.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional) >= 3:
            result = function(session, perspective_id, days)
        elif kwargs:
            result = function(**kwargs)
        else:
            result = function(session, perspective_id, days)

    if inspect.isawaitable(result):
        return await result
    return result


@router.get(
    "/perspectives/event-transitions",
    response_model=PerspectiveEventTransitionsResponse,
)
async def perspective_event_transitions(
    perspective_id: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> PerspectiveEventTransitionsResponse:
    result = await _run_logic(session, perspective_id, days)
    return _normalise_result(result, perspective_id=perspective_id, days=days)


if __name__ == "__main__":
    print("PASS")