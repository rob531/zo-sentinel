"""HTTP surface for the unauthenticated-access inventory service."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import fetch_unauth_inventory

router = APIRouter(prefix="/api/unauth-access", tags=["unauth-access"])


class InventoryItemResponse(BaseModel):
    server_id: str
    url: str | None
    unauth_access: str | None
    source: str | None
    updated_at: str | None
    name: str | None
    verdict: str | None
    risk_tier: str | None
    trust_score: float | None


class InventoryResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[InventoryItemResponse]


@router.get("/inventory", response_model=InventoryResponse)
def get_inventory(
    conn: Session = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    unauth_only: bool = Query(default=True),
    tier: str | None = Query(default=None),
) -> InventoryResponse:
    result = fetch_unauth_inventory(
        conn,
        limit=limit,
        offset=offset,
        unauth_only=unauth_only,
        tier=tier,
    )
    return InventoryResponse(**result)


if __name__ == "__main__":
    print("PASS")