"""HTTP surface for the axis p-top benchmark service."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_benchmarks

router = APIRouter(prefix="/api", tags=["axis-benchmark"])


@router.get("/axis/benchmark")
def get_axis_benchmark(
    window_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return axis_benchmarks(db, window_days=window_days)