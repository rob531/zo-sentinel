# deps: fastapi, pydantic, sqlalchemy
"""dispute_reason_category_breakdown -- breakdown of score disputes by reason category.

GET /api/disputes/reason-category-breakdown
  Dispute counts and resolution stats grouped by reason_category.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

router = APIRouter(prefix="/api", tags=["dispute_reason_category_breakdown"])


# --------------------------------------------------------------------------- #
# Response shapes
# --------------------------------------------------------------------------- #

class CategoryBreakdownRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reason_category: str
    total_count: int
    pending_count: int
    open_count: int
    resolved_count: int
    resolution_rate: float
    avg_resolution_days: float


class CategoryBreakdownResponse(BaseModel):
    items: list[CategoryBreakdownRow]
    total_categories: int


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _compute_avg_resolution_days(db: Session, reason_category: str) -> float:
    if resolved := db.scalar(
        select(func.count(McpScoreDispute.id))
        .where(McpScoreDispute.reason_category == reason_category)
        .where(McpScoreDispute.status == "resolved")
    ) or 0:
        row = db.execute(
            select(
                func.avg(
                    func.julianday(McpScoreDispute.resolved_at)
                    - func.julianday(McpScoreDispute.created_at)
                )
            ).where(McpScoreDispute.reason_category == reason_category)
            .where(McpScoreDispute.status == "resolved")
        ).scalar_one_or_none()
        return round(float(row), 2) if row is not None else 0.0
    return 0.0


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/disputes/reason-category-breakdown", response_model=CategoryBreakdownResponse)
def get_reason_category_breakdown(
    db: Session = Depends(get_session),
    limit: int = Query(20, ge=1, le=100, description="Max categories to return"),
) -> CategoryBreakdownResponse:
    """Return dispute counts and resolution stats grouped by reason_category, descending by total."""
    rows = db.execute(
        select(
            McpScoreDispute.reason_category,
            func.count(McpScoreDispute.id).label("total_count"),
            func.sum(
                func.cast(McpScoreDispute.status == "pending", int)
            ).label("pending_count"),
            func.sum(
                func.cast(McpScoreDispute.status == "open", int)
            ).label("open_count"),
            func.sum(
                func.cast(McpScoreDispute.status == "resolved", int)
            ).label("resolved_count"),
        )
        .group_by(McpScoreDispute.reason_category)
        .order_by(func.count(McpScoreDispute.id).desc())
        .limit(limit)
    ).all()

    items = []
    for row in rows:
        cat = row._mapping
        total = cat["total_count"]
        resolved = cat["resolved_count"]
        resolution_rate = round((resolved / total) * 100, 2) if total else 0.0
        avg_days = _compute_avg_resolution_days(db, cat["reason_category"])
        items.append(
            CategoryBreakdownRow(
                reason_category=cat["reason_category"] or "unknown",
                total_count=total,
                pending_count=cat["pending_count"],
                open_count=cat["open_count"],
                resolved_count=resolved,
                resolution_rate=resolution_rate,
                avg_resolution_days=avg_days,
            )
        )

    return CategoryBreakdownResponse(
        items=items,
        total_categories=len(items),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    sys.path.insert(0, "/home/workspace/zo_sentinel")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker, declarative_base
    from sqlalchemy.pool import StaticPool

    Base = declarative_base()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                server_id VARCHAR(128) PRIMARY KEY,
                name VARCHAR(256)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_score_disputes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id VARCHAR(128) NOT NULL,
                submitted_by VARCHAR(128) NOT NULL,
                proposed_overall_risk VARCHAR(16),
                proposed_axes TEXT,
                reason_category VARCHAR(48),
                explanation TEXT,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                admin_note TEXT,
                created_at TIMESTAMP NOT NULL,
                resolved_at TIMESTAMP
            )
        """))
        conn.commit()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_session():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    from datetime import timedelta
    now = datetime.now(timezone.utc)

    with SessionLocal() as sess:
        sess.execute(
            text("INSERT INTO mcp_server_registry VALUES (:sid, :name)"),
            {"sid": "srv_x", "name": "Server X"},
        )
        # Category A: 2 disputes (1 resolved, 1 open)
        sess.execute(text("""
            INSERT INTO mcp_score_disputes
                (server_id, submitted_by, proposed_overall_risk, reason_category,
                 explanation, status, created_at, resolved_at)
            VALUES (:sid, :sb, :risk, :cat, :exp, :st, :ca, :ra)
        """), {
            "sid": "srv_x", "sb": "u1", "risk": "HIGH",
            "cat": "incorrect_category", "exp": "test",
            "st": "open", "ca": now - timedelta(days=5), "ra": None,
        })
        sess.execute(text("""
            INSERT INTO mcp_score_disputes
                (server_id, submitted_by, proposed_overall_risk, reason_category,
                 explanation, status, created_at, resolved_at)
            VALUES (:sid, :sb, :risk, :cat, :exp, :st, :ca, :ra)
        """), {
            "sid": "srv_x", "sb": "u2", "risk": "LOW",
            "cat": "incorrect_category", "exp": "test",
            "st": "resolved", "ca": now - timedelta(days=4), "ra": now - timedelta(days=3),
        })
        # Category B: 1 dispute (pending)
        sess.execute(text("""
            INSERT INTO mcp_score_disputes
                (server_id, submitted_by, proposed_overall_risk, reason_category,
                 explanation, status, created_at, resolved_at)
            VALUES (:sid, :sb, :risk, :cat, :exp, :st, :ca, :ra)
        """), {
            "sid": "srv_x", "sb": "u3", "risk": "MEDIUM",
            "cat": "outdated_score", "exp": "test",
            "st": "pending", "ca": now - timedelta(days=2), "ra": None,
        })
        # Category C: 1 dispute (resolved)
        sess.execute(text("""
            INSERT INTO mcp_score_disputes
                (server_id, submitted_by, proposed_overall_risk, reason_category,
                 explanation, status, created_at, resolved_at)
            VALUES (:sid, :sb, :risk, :cat, :exp, :st, :ca, :ra)
        """), {
            "sid": "srv_x", "sb": "u4", "risk": "LOW",
            "cat": "missing_data", "exp": "test",
            "st": "resolved", "ca": now - timedelta(days=3), "ra": now - timedelta(days=1),
        })
        sess.commit()

    client = TestClient(app)

    r = client.get("/api/disputes/reason-category-breakdown")
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["total_categories"] == 3, f"total_categories: {data['total_categories']}"
    items = data["items"]

    # Top category should be "incorrect_category" (count=2)
    assert items[0]["reason_category"] == "incorrect_category"
    assert items[0]["total_count"] == 2
    assert items[0]["open_count"] == 1
    assert items[0]["resolved_count"] == 1
    assert items[0]["resolution_rate"] == 50.0
    assert items[0]["avg_resolution_days"] == 1.0

    # Second: "outdated_score" (count=1, all pending)
    assert items[1]["reason_category"] == "outdated_score"
    assert items[1]["total_count"] == 1
    assert items[1]["pending_count"] == 1
    assert items[1]["resolved_count"] == 0
    assert items[1]["resolution_rate"] == 0.0

    # Third: "missing_data" (count=1, resolved)
    assert items[2]["reason_category"] == "missing_data"
    assert items[2]["total_count"] == 1
    assert items[2]["resolved_count"] == 1
    assert items[2]["avg_resolution_days"] == 2.0

    # Test limit param
    r2 = client.get("/api/disputes/reason-category-breakdown?limit=2")
    assert r2.status_code == 200
    assert r2.json()["total_categories"] == 2

    print("PASS")
