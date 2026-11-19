from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot
from .logic import *  # noqa: F401,F403

router = APIRouter(prefix="/api", tags=["perspective-snapshot-ageing"])


class PerspectiveSnapshotAgeing(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    perspective_id: str
    name: str
    last_snapshot_at: datetime | None
    snapshot_age_hours: float
    snapshot_count: int
    membership_size: int
    unseen_event_count: int
    tier_change_count: int


class PerspectiveSnapshotAgeingResponse(BaseModel):
    generated_at: datetime
    perspectives: list[PerspectiveSnapshotAgeing]


_LOGIC_FUNCTIONS = {
    name: value
    for name, value in globals().items()
    if callable(value)
    and not name.startswith("_")
    and (
        "snapshot_ageing" in name
        or "snapshot_aging" in name
        or "perspective_ageing" in name
        or "perspective_aging" in name
    )
}


def _get_snapshot_ageing(db: Session) -> Any:
    preferred_names = (
        "get_perspective_snapshot_ageing",
        "compute_perspective_snapshot_ageing",
        "build_perspective_snapshot_ageing",
        "get_snapshot_ageing",
        "compute_snapshot_ageing",
        "build_snapshot_ageing",
        "perspective_snapshot_ageing",
        "snapshot_ageing",
    )
    for name in preferred_names:
        function = _LOGIC_FUNCTIONS.get(name)
        if function is not None:
            return function(db)
    if _LOGIC_FUNCTIONS:
        return next(iter(_LOGIC_FUNCTIONS.values()))(db)
    raise RuntimeError("No snapshot ageing function is exposed by the service logic")


def _normalise_report(result: Any) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)

    if isinstance(result, dict):
        report = dict(result)
    elif isinstance(result, tuple) and len(result) == 2:
        report = {"generated_at": result[0], "perspectives": result[1]}
    elif hasattr(result, "perspectives"):
        report = {
            "generated_at": getattr(result, "generated_at", generated_at),
            "perspectives": result.perspectives,
        }
    else:
        report = {"generated_at": generated_at, "perspectives": result}

    report.setdefault("generated_at", generated_at)
    report.setdefault("perspectives", [])
    return report


@router.get(
    "/perspective/snapshot-ageing",
    response_model=PerspectiveSnapshotAgeingResponse,
)
def read_perspective_snapshot_ageing(
    db: Session = Depends(get_session),
) -> PerspectiveSnapshotAgeingResponse:
    report = _normalise_report(_get_snapshot_ageing(db))
    return PerspectiveSnapshotAgeingResponse.model_validate(report)