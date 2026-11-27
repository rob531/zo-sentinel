"""logic.py -- the score-dispute triage data/computation layer.

Mirrors the exemplar doctrine: import the REAL data layer (app.db/app.models),
never an inline/stub/SQLite-dict model. Pure functions over a Session so the
router stays thin and the contract can exercise this directly.

Tables used (read-only):
  mcp_score_disputes   -> open disputes submitted by submitters
  mcp_server_registry  -> current risk tier of the disputed server
  mcp_llm_axis_scores  -> latest 'overall_risk' axis score per server
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry

__all__ = [
    "get_dispute_triage",
    "triage_disputes",
    "get_current_risk_tier",
    "get_latest_axis_p_top",
    "get_open_disputes_by_reason",
]

OVERALL_RISK_AXIS = "overall_risk"


def _normalize_tier(value: Optional[str]) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def get_current_risk_tier(db: Session, server_id: str) -> Optional[str]:
    """Current risk_tier for a server from the real registry (SELECT only)."""
    row = db.execute(
        select(McpServerRegistry.risk_tier).where(
            McpServerRegistry.server_id == server_id
        )
    ).first()
    if row is None:
        return None
    return row[0]


def get_latest_axis_p_top(db: Session, server_id: str) -> Optional[float]:
    """p_top of the most recent 'overall_risk' axis row for a server."""
    row = db.execute(
        select(McpLlmAxisScore.p_top)
        .where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == OVERALL_RISK_AXIS,
        )
        .order_by(McpLlmAxisScore.scored_at.desc().nullslast(), McpLlmAxisScore.id.desc())
        .limit(1)
    ).first()
    if row is None or row[0] is None:
        return None
    return float(row[0])


def _latest_axis_map(db: Session, server_ids: List[str]) -> Dict[str, float]:
    """Map server_id -> p_top of the latest overall_risk axis row."""
    if not server_ids:
        return {}
    rows = db.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.p_top,
        )
        .where(
            McpLlmAxisScore.server_id.in_(server_ids),
            McpLlmAxisScore.axis_name == OVERALL_RISK_AXIS,
        )
        .order_by(McpLlmAxisScore.scored_at.desc().nullslast(), McpLlmAxisScore.id.desc())
    ).all()
    out: Dict[str, float] = {}
    for server_id, p_top in rows:
        if server_id in out:
            continue
        if p_top is None:
            continue
        out[server_id] = float(p_top)
    return out


def get_open_disputes_by_reason(db: Session, status: Optional[str] = "open") -> Dict[str, int]:
    """Count disputes per reason_category for the given status filter."""
    stmt = select(McpScoreDispute.reason_category, func.count()).group_by(
        McpScoreDispute.reason_category
    )
    if status not in (None, "", "all"):
        stmt = stmt.where(McpScoreDispute.status == status)
    rows = db.execute(stmt).all()
    out: Dict[str, int] = {}
    for reason, count in rows:
        out[reason or "unknown"] = int(count)
    return out


def get_dispute_triage(
    db: Session,
    status: Optional[str] = "open",
    limit: int = 50,
) -> Dict[str, Any]:
    """Triage disputes: totals, counts by reason_category, and tier-shift items.

    All filters are applied as bound parameters through the ORM; no SQL string
    interpolation, no writes.
    """
    if limit is None or int(limit) < 0:
        limit = 0
    limit = int(limit)

    count_stmt = select(func.count()).select_from(McpScoreDispute)
    list_stmt = select(McpScoreDispute)
    if status not in (None, "", "all"):
        count_stmt = count_stmt.where(McpScoreDispute.status == status)
        list_stmt = list_stmt.where(McpScoreDispute.status == status)

    total = int(db.execute(count_stmt).scalar() or 0)
    by_reason = get_open_disputes_by_reason(db, status)

    disputes = db.execute(
        list_stmt.order_by(
            McpScoreDispute.created_at.desc().nullslast(), McpScoreDispute.id.desc()
        ).limit(limit)
    ).scalars().all()

    server_ids = sorted({d.server_id for d in disputes if d.server_id})
    tiers: Dict[str, Optional[str]] = {}
    if server_ids:
        tier_rows = db.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.risk_tier).where(
                McpServerRegistry.server_id.in_(server_ids)
            )
        ).all()
        for server_id, risk_tier in tier_rows:
            tiers[server_id] = risk_tier
    _latest_axis_map(db, server_ids)  # latest overall_risk join (read-only)

    items: List[Dict[str, Any]] = []
    for d in disputes:
        current_tier = tiers.get(d.server_id)
        proposed = _normalize_tier(d.proposed_overall_risk)
        current = _normalize_tier(current_tier)
        tier_shift = bool(current) and proposed != current
        created_at = getattr(d, "created_at", None)
        items.append(
            {
                "id": d.id,
                "server_id": d.server_id,
                "proposed_overall_risk": d.proposed_overall_risk,
                "current_risk_tier": current_tier,
                "tier_shift": tier_shift,
                "reason_category": d.reason_category,
                "status": d.status,
                "created_at": created_at.isoformat() if isinstance(created_at, datetime) else created_at,
            }
        )

    return {"total": total, "by_reason": by_reason, "items": items}


def triage_disputes(
    db: Session,
    status: Optional[str] = "open",
    limit: int = 50,
) -> Dict[str, Any]:
    """Alias of get_dispute_triage."""
    return get_dispute_triage(db, status=status, limit=limit)


def _self_test() -> None:
    """In-memory SQLite self-test of the triage computation."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    def _make_sqlite_compatible() -> None:
        try:
            from sqlalchemy import JSON
        except Exception:  # pragma: no cover
            return
        for table in McpScoreDispute.metadata.sorted_tables:
            for col in table.columns:
                if type(col.type).__name__ == "JSONB":
                    col.type = JSON()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _make_sqlite_compatible()
    McpScoreDispute.metadata.create_all(engine, checkfirst=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    seed = SessionLocal()
    try:
        seed.add(
            McpServerRegistry(
                server_id="srv-alpha",
                name="alpha",
                risk_tier="high",
                trust_score=0.4,
                verdict="risky",
            )
        )
        seed.add(
            McpServerRegistry(
                server_id="srv-beta",
                name="beta",
                risk_tier="low",
                trust_score=0.9,
                verdict="clean",
            )
        )
        seed.add(
            McpLlmAxisScore(
                id=1,
                server_id="srv-alpha",
                axis_name="overall_risk",
                model_version="v1",
                p_top=0.95,
                scored_at=now,
            )
        )
        seed.add(
            McpLlmAxisScore(
                id=2,
                server_id="srv-alpha",
                axis_name="overall_risk",
                model_version="v2",
                p_top=0.62,
                scored_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
            )
        )
        seed.add(
            McpLlmAxisScore(
                id=3,
                server_id="srv-beta",
                axis_name="overall_risk",
                model_version="v1",
                p_top=0.11,
                scored_at=now,
            )
        )
        seed.add(
            McpScoreDispute(
                id=1,
                server_id="srv-alpha",
                submitted_by="user-1",
                proposed_overall_risk="medium",
                proposed_axes="{}",
                reason_category="model_uncertainty",
                explanation="model disagrees with tier",
                status="open",
                created_at=now,
            )
        )
        seed.add(
            McpScoreDispute(
                id=2,
                server_id="srv-alpha",
                submitted_by="user-2",
                proposed_overall_risk="high",
                proposed_axes="{}",
                reason_category="model_uncertainty",
                explanation="agrees with tier",
                status="open",
                created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            )
        )
        seed.add(
            McpScoreDispute(
                id=3,
                server_id="srv-beta",
                submitted_by="user-3",
                proposed_overall_risk="critical",
                proposed_axes="{}",
                reason_category="data_conflict",
                explanation="stale registry entry",
                status="open",
                created_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            )
        )
        seed.commit()
    finally:
        seed.close()

    local_app = FastAPI()

    @local_app.get("/api/disputes/triage")
    def _triage(status: str = "open", limit: int = 50, db: Session = Depends(get_session)):
        return get_dispute_triage(db, status=status, limit=limit)

    def _override():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    local_app.dependency_overrides[get_session] = _override

    client = TestClient(local_app)
    resp = client.get("/api/disputes/triage", params={"status": "open", "limit": 50})
    assert resp.status_code == 200, resp.status_code
    body = resp.json()
    assert body["total"] == 3, body
    assert body["by_reason"] == {"model_uncertainty": 2, "data_conflict": 1}, body
    assert len(body["items"]) == 3, body
    assert any(item["tier_shift"] for item in body["items"]), body
    print("PASS")


if __name__ == "__main__":
    _self_test()