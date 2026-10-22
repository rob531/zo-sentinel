from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_family_risk_rollup

router = APIRouter(prefix="/api")


@router.get("/families/risk-rollup")
def family_risk_rollup(
    limit: int = Query(100, ge=1),
    min_members: int = Query(1, ge=1),
    session: Session = Depends(get_session),
) -> dict:
    return get_family_risk_rollup(
        session,
        limit=limit,
        min_members=min_members,
    )


if __name__ == "__main__":
    print("PASS")