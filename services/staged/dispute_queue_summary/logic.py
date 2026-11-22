"""logic.py -- data/computation layer for the dispute_queue_summary service.

Provides a single pure function that returns a summary of score disputes.
All data access is performed via the real SQLAlchemy models from ``app.models``
and the session dependency from ``app.db``.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, desc
from sqlalchemy.orm import Session

from app.db import get_session, Base  # real session factory and metadata
from app.models import McpScoreDispute, McpServerRegistry


def dispute_summary(
    db: Session,
    *,
    status: str = "all",
    limit: int = 50,
) -> Dict[str, Any]:
    """Return a summary of score disputes.

    Args:
        db: SQLAlchemy session.
        status: ``open``, ``resolved`` or ``all`` (default).
        limit: Maximum number of newest disputes to return (1‑200, default 50).

    Returns:
        Mapping matching the contract described in the service spec.
    """
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    # ------------------------------------------------------------------ #
    # Overall totals (ignoring the ``status`` filter, as the spec expects a
    # full‑system view for ``total`` and the grouped counts).
    # ------------------------------------------------------------------ #
    total = db.execute(select(func.count()).select_from(McpScoreDispute)).scalar_one()

    # Count by status
    rows_by_status = db.execute(
        select(McpScoreDispute.status, func.count())
        .group_by(McpScoreDispute.status)
    ).all()
    by_status: Dict[str, int] = {}
    for st, cnt in rows_by_status:
        by_status[st or "unknown"] = int(cnt)

    # Count by reason_category
    rows_by_reason = db.execute(
        select(McpScoreDispute.reason_category, func.count())
        .group_by(McpScoreDispute.reason_category)
    ).all()
    by_reason_category: Dict[str, int] = {}
    for rc, cnt in rows_by_reason:
        by_reason_category[rc or "unknown"] = int(cnt)

    # ------------------------------------------------------------------ #
    # Oldest open dispute age (hours).  If there are no open disputes the
    # value is ``None``.
    # ------------------------------------------------------------------ #
    oldest_open_dt: Optional[datetime] = db.execute(
        select(func.min(McpScoreDispute.created_at))
        .where(McpScoreDispute.status == "open")
    ).scalar_one()
    if oldest_open_dt is not None:
        now = datetime.now(timezone.utc)
        # ensure both datetimes are timezone‑aware
        if oldest_open_dt.tzinfo is None:
            oldest_open_dt = oldest_open_dt.replace(tzinfo=timezone.utc)
        age_seconds = (now - oldest_open_dt).total_seconds()
        oldest_open_age_hours = round(age_seconds / 3600, 2)
    else:
        oldest_open_age_hours = None

    # ------------------------------------------------------------------ #
    # Newest disputes list (subject to the ``status`` filter).
    # ------------------------------------------------------------------ #
    dispute_q = select(
        McpScoreDispute.id,
        McpScoreDispute.server_id,
        McpServerRegistry.name.label("server_name"),
        McpScoreDispute.status,
        McpScoreDispute.reason_category,
        McpScoreDispute.proposed_overall_risk,
        McpScoreDispute.created_at,
        McpScoreDispute.resolved_at,
    ).join(
        McpServerRegistry,
        McpScoreDispute.server_id == McpServerRegistry.server_id,
        isouter=True,
    )
    if status != "all":
        dispute_q = dispute_q.where(McpScoreDispute.status == status)

    dispute_q = dispute_q.order_by(desc(McpScoreDispute.created_at)).limit(limit)

    rows = db.execute(dispute_q).all()
    items: List[Dict[str, Any]] = []
    for (
        did,
        srv_id,
        srv_name,
        st,
        rc,
        por,
        created,
        resolved,
    ) in rows:
        items.append(
            {
                "id": did,
                "server_id": srv_id,
                "server_name": srv_name,
                "status": st,
                "reason_category": rc,
                "proposed_overall_risk": por,
                "created_at": created.isoformat() if created else None,
                "resolved_at": resolved.isoformat() if resolved else None,
            }
        )

    return {
        "total": int(total),
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "oldest_open_age_hours": oldest_open_age_hours,
        "items": items,
    }


# --------------------------------------------------------------------------- #
# Self‑test executed when the module is run directly.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI, Depends
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Build an in‑memory SQLite DB that mirrors the real models.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Dependency override for the test FastAPI app.
    def get_test_session() -> Session:  # pragma: no cover
        with TestSession() as sess:
            yield sess

    app = FastAPI()

    @app.get("/api/disputes/summary")
    def api_summary(
        status: str = "all",
        limit: int = 50,
        db: Session = Depends(get_test_session),
    ):
        return dispute_summary(db, status=status, limit=limit)

    # ------------------------------------------------------------------- #
    # Seed the in‑memory DB with the three disputes and two servers required
    # by the acceptance criteria.
    # ------------------------------------------------------------------- #
    with TestSession() as sess:
        srv1 = McpServerRegistry(
            server_id="srv-1",
            name="Server One",
            risk_tier="high",
        )
        srv2 = McpServerRegistry(
            server_id="srv-2",
            name="Server Two",
            risk_tier="low",
        )
        sess.add_all([srv1, srv2])
        now = datetime.now(timezone.utc)

        d1 = McpScoreDispute(
            id=1,
            server_id="srv-1",
            submitted_by="alice",
            proposed_overall_risk="medium",
            proposed_axes={},
            reason_category="category-a",
            explanation="explain a",
            status="open",
            created_at=now - timedelta(hours=10),
            resolved_at=None,
        )
        d2 = McpScoreDispute(
            id=2,
            server_id="srv-2",
            submitted_by="bob",
            proposed_overall_risk="low",
            proposed_axes={},
            reason_category="category-b",
            explanation="explain b",
            status="resolved",
            created_at=now - timedelta(hours=20),
            resolved_at=now - timedelta(hours=5),
        )
        d3 = McpScoreDispute(
            id=3,
            server_id="srv-1",
            submitted_by="carol",
            proposed_overall_risk="high",
            proposed_axes={},
            reason_category="category-a",
            explanation="explain c",
            status="open",
            created_at=now - timedelta(hours=2),
            resolved_at=None,
        )
        sess.add_all([d1, d2, d3])
        sess.commit()

    # ------------------------------------------------------------------- #
    # Run the contract assertions.
    # ------------------------------------------------------------------- #
    with TestSession() as sess:
        result = dispute_summary(sess, status="all", limit=2)

        assert result["total"] == 3, "total should be 3"
        assert result["by_status"].get("open", 0) == 2, "open count should be 2"
        assert len(result["items"]) == 2, "should respect limit=2"
        for itm in result["items"]:
            assert "server_name" in itm and itm["server_name"] is not None, "server_name missing"

    print("PASS")