from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from .logic import get_ingestion_lag

router = APIRouter(prefix="/api")


@router.get("/scores/ingestion-lag")
def axis_score_ingestion_lag(
    hours: int = Query(default=168),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_ingestion_lag(session=session, hours=hours)


if __name__ == "__main__":
    print("PASS")