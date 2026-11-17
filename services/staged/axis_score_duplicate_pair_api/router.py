from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

try:
    from .logic import get_duplicate_pairs
except ImportError:
    from .logic import get_axis_score_duplicate_pairs as get_duplicate_pairs


class DuplicatePairGroup(BaseModel):
    server_id: str
    axis_name: str
    row_count: int
    latest_scored_at: datetime | None
    decision_rule_versions: list[str]
    model_versions: list[str]


class DuplicatePairResponse(BaseModel):
    groups: list[DuplicatePairGroup]
    total_groups: int


router = APIRouter(prefix="/api")


@router.get(
    "/axis-scores/duplicate-pairs",
    response_model=DuplicatePairResponse,
)
def get_duplicate_axis_score_pairs(
    session: Session = Depends(get_session),
) -> DuplicatePairResponse:
    result: Any = get_duplicate_pairs(session)

    if isinstance(result, dict):
        groups = result.get("groups", [])
    else:
        groups = result

    normalized_groups = [
        DuplicatePairGroup(
            server_id=group["server_id"],
            axis_name=group["axis_name"],
            row_count=group["row_count"],
            latest_scored_at=group.get("latest_scored_at"),
            decision_rule_versions=list(group.get("decision_rule_versions") or []),
            model_versions=list(group.get("model_versions") or []),
        )
        for group in groups
    ]

    return DuplicatePairResponse(
        groups=normalized_groups,
        total_groups=len(normalized_groups),
    )


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    assert any(
        route.path == "/api/axis-scores/duplicate-pairs"
        for route in test_app.routes
    )
    print("PASS")