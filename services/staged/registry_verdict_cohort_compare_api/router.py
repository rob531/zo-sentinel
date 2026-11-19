"""HTTP surface for registry verdict cohort comparisons."""
from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import verdict_cohort_compare
except ImportError:  # pragma: no cover
    try:
        from .logic import get_verdict_cohort_compare as verdict_cohort_compare
    except ImportError:  # pragma: no cover
        from .logic import registry_verdict_cohort_compare as verdict_cohort_compare

router = APIRouter(prefix="/api/registry", tags=["registry"])


class VerdictCohort(BaseModel):
    source: str
    total: int
    verdict_counts: dict[str, int]
    tier_counts: dict[str, int]
    mean_trust_score: float


class VerdictCohortCompareResponse(BaseModel):
    days: int
    cohorts: list[VerdictCohort]


def _call_logic(db: Session, source: str | None, days: int) -> Any:
    parameters = inspect.signature(verdict_cohort_compare).parameters
    kwargs: dict[str, Any] = {}

    if "days" in parameters:
        kwargs["days"] = days
    if "source" in parameters:
        kwargs["source"] = source
    elif "registry_source" in parameters:
        kwargs["registry_source"] = source

    if kwargs:
        return verdict_cohort_compare(db, **kwargs)
    return verdict_cohort_compare(db, source, days)


def _normalize_cohorts(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, Mapping):
        cohorts = result.get("cohorts", [])
    elif isinstance(result, tuple) and len(result) == 2:
        cohorts = result[1]
    elif isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
        cohorts = result
    else:
        cohorts = []

    normalized: list[dict[str, Any]] = []
    for cohort in cohorts:
        if not isinstance(cohort, Mapping):
            cohort = {
                "source": getattr(cohort, "source", ""),
                "total": getattr(cohort, "total", 0),
                "verdict_counts": getattr(cohort, "verdict_counts", {}),
                "tier_counts": getattr(cohort, "tier_counts", {}),
                "mean_trust_score": getattr(cohort, "mean_trust_score", 0.0),
            }

        normalized.append(
            {
                "source": str(cohort.get("source", cohort.get("registry_source", ""))),
                "total": int(cohort.get("total", 0)),
                "verdict_counts": {
                    str(key): int(value)
                    for key, value in (cohort.get("verdict_counts") or {}).items()
                },
                "tier_counts": {
                    str(key): int(value)
                    for key, value in (
                        cohort.get("tier_counts")
                        or cohort.get("risk_tier_counts")
                        or {}
                    ).items()
                },
                "mean_trust_score": float(
                    cohort.get("mean_trust_score", cohort.get("trust_score", 0.0))
                ),
            }
        )
    return normalized


@router.get(
    "/verdict-cohort-compare",
    response_model=VerdictCohortCompareResponse,
)
def get_verdict_cohort_compare(
    source: str | None = Query(default=None),
    days: int = Query(default=30, ge=1),
    db: Session = Depends(get_session),
) -> VerdictCohortCompareResponse:
    result = _call_logic(db, source, days)
    return VerdictCohortCompareResponse(
        days=days,
        cohorts=_normalize_cohorts(result),
    )