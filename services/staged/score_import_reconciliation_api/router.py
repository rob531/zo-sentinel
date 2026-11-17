"""HTTP surface for score import reconciliation reporting."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import reconciliation_report

router = APIRouter(prefix="/api", tags=["score_import_reconciliation_api"])


class SourceReconciliation(BaseModel):
    registry_source: str | None = None
    servers: int
    scored: int
    never_scored: int
    stale_registry: int
    mean_lag_seconds: float | None = None


class ReconciliationTotals(BaseModel):
    servers: int
    never_scored: int
    stale_registry: int


class ReconciliationResponse(BaseModel):
    sources: list[SourceReconciliation]
    totals: ReconciliationTotals


@router.get("/scores/reconciliation", response_model=ReconciliationResponse)
def get_reconciliation(db: Session = Depends(get_session)) -> ReconciliationResponse:
    report: Any = reconciliation_report(db)

    if isinstance(report, Mapping):
        source_rows = report.get("sources", [])
        total_values = report.get("totals")
    elif isinstance(report, tuple) and len(report) == 2:
        source_rows, total_values = report
    else:
        source_rows = report
        total_values = None

    sources = [SourceReconciliation.model_validate(row) for row in source_rows]

    if total_values is None:
        total_values = {
            "servers": sum(row.servers for row in sources),
            "never_scored": sum(row.never_scored for row in sources),
            "stale_registry": sum(row.stale_registry for row in sources),
        }

    return ReconciliationResponse(
        sources=sources,
        totals=ReconciliationTotals.model_validate(total_values),
    )