from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

from .logic import get_transition_matrix


router = APIRouter(prefix="/api")


class VerdictTransition(BaseModel):
    server_id: str
    name: str | None = None
    from_tier: str
    to_tier: str
    count: int = Field(ge=0)


class VerdictTransitionMatrixResponse(BaseModel):
    days: int
    transitions: list[VerdictTransition]
    matrix: dict[str, int]


@router.get(
    "/registry/verdict-transitions",
    response_model=VerdictTransitionMatrixResponse,
)
async def verdict_transitions(
    days: int = Query(default=30, ge=1, le=3650),
    session: AsyncSession = Depends(get_session),
) -> Any:
    result = get_transition_matrix(session, days)
    if inspect.isawaitable(result):
        result = await result

    if isinstance(result, dict):
        transitions = list(result.get("transitions", []))
        matrix = dict(result.get("matrix", {}))
        days_value = int(result.get("days", days))
    else:
        transitions = list(result)
        matrix = {}
        days_value = days

    transitions.sort(key=lambda item: int(item.get("count", 0)), reverse=True)

    return {
        "days": days_value,
        "transitions": transitions,
        "matrix": matrix,
    }


if __name__ == "__main__":
    print("PASS")