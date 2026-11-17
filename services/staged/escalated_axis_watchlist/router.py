"""HTTP surface for the escalated LLM-axis watchlist service."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import build_rows


router = APIRouter(prefix="/api/escalated", tags=["escalated-axis-watchlist"])


class AxisRowResponse(BaseModel):
    server_id: str
    name: str | None
    axis_name: str
    label: str | None
    p_top: float | None
    p_critical: float | None
    p_danger: float | None
    escalated_to: str | None
    decision_rule_version: str | None
    model_version: str
    scored_at: str | None
    risk_tier: str | None
    verdict: str | None


class AxisListResponse(BaseModel):
    days: int
    count: int
    rows: list[AxisRowResponse]


class ServerAxisListResponse(BaseModel):
    server_id: str
    count: int
    rows: list[AxisRowResponse]


@router.get("/axes", response_model=AxisListResponse)
def list_escalated_axes(
    days: int = 30,
    limit: int = 100,
    db: Session = Depends(get_session),
) -> AxisListResponse:
    if days <= 0:
        raise HTTPException(status_code=400, detail="days must be greater than zero")
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than zero")

    rows = build_rows(db, days=days, limit=limit)
    return AxisListResponse(days=days, count=len(rows), rows=rows)


@router.get("/axes/{server_id}", response_model=ServerAxisListResponse)
def list_escalated_axes_for_server(
    server_id: str,
    days: int = 30,
    limit: int = 100,
    db: Session = Depends(get_session),
) -> ServerAxisListResponse:
    if days <= 0:
        raise HTTPException(status_code=400, detail="days must be greater than zero")
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than zero")

    if db.get(McpServerRegistry, server_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown server_id: {server_id}")

    rows = build_rows(db, days=days, limit=limit, server_id=server_id)
    return ServerAxisListResponse(server_id=server_id, count=len(rows), rows=rows)