from __future__ import annotations

import inspect
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import AskCorpusDoc

from .logic import get_indexed_at_cohort

router = APIRouter(prefix="/api")


class IndexedAtCohortEntry(BaseModel):
    date: str
    docs: int
    servers: int


class IndexedAtCohortResponse(BaseModel):
    days: int
    total_docs: int
    distinct_servers: int
    cohorts: list[IndexedAtCohortEntry]


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError("Indexed-at-cohort logic returned an unsupported result")


@router.get(
    "/ask-corpus/indexed-at-cohort",
    response_model=IndexedAtCohortResponse,
)
async def indexed_at_cohort(
    days: int = Query(default=30, ge=1, le=3650),
    bucket: Literal["day", "week"] = Query(default="day"),
    session=Depends(get_session),
) -> IndexedAtCohortResponse:
    result = get_indexed_at_cohort(session=session, days=days, bucket=bucket)
    if inspect.isawaitable(result):
        result = await result

    payload = _as_dict(result)
    return IndexedAtCohortResponse(
        days=days,
        total_docs=int(payload.get("total_docs", 0)),
        distinct_servers=int(payload.get("distinct_servers", 0)),
        cohorts=payload.get("cohorts", []),
    )