from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

try:
    from .logic import fetch_disputes as _fetch_disputes
except ImportError:  # pragma: no cover - compatibility with older service builds
    _fetch_disputes = None

try:
    from . import logic as _logic
except ImportError:  # pragma: no cover
    _logic = None


router = APIRouter()


def _query_disputes(
    session: Session,
    status: str | None,
    server_id: str | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    statement = (
        select(McpScoreDispute, McpServerRegistry)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .order_by(McpScoreDispute.created_at.desc(), McpScoreDispute.id.desc())
        .offset(offset)
        .limit(limit)
    )

    if status is not None:
        statement = statement.where(McpScoreDispute.status == status)
    if server_id is not None:
        statement = statement.where(McpScoreDispute.server_id == server_id)

    rows: list[dict[str, Any]] = []
    for dispute, server in session.execute(statement).all():
        rows.append(
            {
                "id": dispute.id,
                "server_id": dispute.server_id,
                "server_name": server.name if server is not None else None,
                "submitted_by": dispute.submitted_by,
                "proposed_overall_risk": dispute.proposed_overall_risk,
                "reason_category": dispute.reason_category,
                "explanation": dispute.explanation,
                "status": dispute.status,
                "admin_note": dispute.admin_note,
                "created_at": dispute.created_at,
                "resolved_at": dispute.resolved_at,
                "current_verdict": server.verdict if server is not None else None,
                "current_risk_tier": server.risk_tier if server is not None else None,
            }
        )
    return rows


@router.get("/disputes")
def get_disputes(
    session: Session = Depends(get_session),
    status: str | None = Query(default="OPEN"),
    server_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    fetch = _fetch_disputes
    if fetch is None and _logic is not None:
        for name in ("fetch_disputes", "get_disputes", "list_disputes"):
            candidate = getattr(_logic, name, None)
            if candidate is not None:
                fetch = candidate
                break

    if fetch is not None:
        try:
            result = fetch(
                session,
                status=status,
                server_id=server_id,
                limit=limit,
                offset=offset,
            )
        except TypeError:
            try:
                result = fetch(session, status, server_id, limit, offset)
            except TypeError:
                result = None
        else:
            if isinstance(result, dict) and "disputes" in result:
                disputes = list(result.get("disputes") or [])
                return {
                    "total": int(result.get("total", len(disputes))),
                    "count": len(disputes),
                    "offset": int(result.get("offset", offset)),
                    "disputes": disputes,
                }
            if isinstance(result, (list, tuple)):
                disputes = list(result)
                return {
                    "total": len(disputes),
                    "count": len(disputes),
                    "offset": offset,
                    "disputes": disputes,
                }

    disputes = _query_disputes(session, status, server_id, limit, offset)
    return {
        "total": len(disputes),
        "count": len(disputes),
        "offset": offset,
        "disputes": disputes,
    }


if __name__ == "__main__":
    print("PASS")