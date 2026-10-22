from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_cost_ledger

router = APIRouter(prefix="/api", tags=["scoring"])


class CostLedgerDay(BaseModel):
    date: date
    axis_rows: int
    servers: int


class CostLedgerTotals(BaseModel):
    axis_rows: int
    servers: int
    days: int


class CostLedgerResponse(BaseModel):
    days: list[CostLedgerDay]
    totals: CostLedgerTotals


@router.get("/scoring/cost-ledger", response_model=CostLedgerResponse)
def read_cost_ledger(
    days: int = Query(default=30, ge=1, le=3660),
    session: Session = Depends(get_session),
) -> CostLedgerResponse:
    return get_cost_ledger(session=session, days=days)