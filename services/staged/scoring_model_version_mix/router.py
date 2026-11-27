"""HTTP surface for scoring model-version mix reporting."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import model_version_mix

router = APIRouter(prefix="/api/scoring", tags=["scoring"])


class VersionMixEntry(BaseModel):
    model_version: str
    decision_rule_version: str
    adapter_sha256: str
    rows: int
    servers: int
    last_scored_at: str
    axis_breakdown: dict[str, int]


class ModelVersionMixResponse(BaseModel):
    days: int
    total_rows: int
    versions: list[VersionMixEntry]


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


@router.get("/model-version-mix", response_model=ModelVersionMixResponse)
def get_model_version_mix(
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(get_session),
) -> ModelVersionMixResponse:
    result = model_version_mix(db, days)

    if isinstance(result, dict):
        raw_versions = result.get("versions", [])
        total_rows = result.get("total_rows")
    else:
        raw_versions = result
        total_rows = None

    versions = [
        VersionMixEntry(
            model_version=str(_field(item, "model_version", "")),
            decision_rule_version=str(_field(item, "decision_rule_version", "")),
            adapter_sha256=str(_field(item, "adapter_sha256", "")),
            rows=int(_field(item, "rows", 0)),
            servers=int(_field(item, "servers", 0)),
            last_scored_at=str(_field(item, "last_scored_at", "")),
            axis_breakdown={
                str(axis): int(count)
                for axis, count in (_field(item, "axis_breakdown", {}) or {}).items()
            },
        )
        for item in raw_versions
    ]

    if total_rows is None:
        total_rows = sum(version.rows for version in versions)

    return ModelVersionMixResponse(
        days=days,
        total_rows=int(total_rows),
        versions=versions,
    )