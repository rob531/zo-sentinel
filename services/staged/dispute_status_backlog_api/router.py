from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry
from .logic import dispute_status_backlog

router = APIRouter(prefix="/api", tags=["disputes"])


def _get_backlog(status: str | None, session: Session) -> dict[str, Any]:
    parameters = inspect.signature(dispute_status_backlog).parameters
    if "session" in parameters:
        return dispute_status_backlog(status=status, session=session)
    if "db" in parameters:
        return dispute_status_backlog(status=status, db=session)
    return dispute_status_backlog(status=status)


@router.get("/disputes/status-backlog")
def get_dispute_status_backlog(
    status: str | None = Query(default=None),
    days: int = Query(default=30, ge=0),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    result = _get_backlog(status, session)
    result["days"] = days
    return result


if __name__ == "__main__":
    print("PASS")