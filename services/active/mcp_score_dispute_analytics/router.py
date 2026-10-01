# deps: fastapi, pydantic, sqlalchemy
"""mcp_score_dispute_analytics -- analytics and trends for score disputes.

GET /api/disputes/analytics/overview
  Total disputes, breakdown by status, resolution rate, avg resolution time.

GET /api/disputes/analytics/by-category
  Dispute counts grouped by reason_category, descending.

GET /api/disputes/analytics/recent
  Up to 50 most recent disputes with server names (outer join).

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

router = APIRouter(prefix="/api", tags=["mcp_score_dispute_analytics"])


# --------------------------------------------------------------------------- #
# Response shapes
# --------------------------------------------------------------------------- #

class DisputeOverview(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total: int
    pending: int
    open: int
    resolved: int
    resolution_rate: float
    avg_resolution_days: float


class CategoryCount(BaseModel):
    reason_category: str
    count: int


class DisputeWithServer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    server_id: str
    server_name: Optional[str] = None
    proposed_overall_risk: Optional[str] = None
    reason_category: Optional[str] = None
    status: str
    created_at: datetime
    resolved_at: Optional[datetime] = None


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/disputes/analytics/overview", response_model=DisputeOverview)
def get_overview(db: Session = Depends(get_session)) -> DisputeOverview:
    """Return aggregate dispute counts, resolution rate, and avg resolution time."""
    total = db.scalar(select(func.count(McpScoreDispute.id))) or 0

    pending = db.scalar(
        select(func.count(McpScoreDispute.id)).where(McpScoreDispute.status == "pending")
    ) or 0
    open_ = db.scalar(
        select(func.count(McpScoreDispute.id)).where(McpScoreDispute.status == "open")
    ) or 0
    resolved = db.scalar(
        select(func.count(McpScoreDispute.id)).where(McpScoreDispute.status == "resolved")
    ) or 0

    resolution_rate = round((resolved / total) * 100, 2) if total else 0.0

    avg_days = 0.0
    if resolved > 0:
        row = db.execute(
            select(
                func.avg(
                    func.julianday(McpScoreDispute.resolved_at)
                    - func.julianday(McpScoreDispute.created_at)
                )
            ).where(McpScoreDispute.status == "resolved")
        ).scalar_one_or_none()
        avg_days = round(float(row), 2) if row is not None else 0.0

    return DisputeOverview(
        total=total,
        pending=pending,
        open=open_,
        resolved=resolved,
        resolution_rate=resolution_rate,
        avg_resolution_days=avg_days,
    )


@router.get("/disputes/analytics/by-category", response_model=list[CategoryCount])
def get_by_category(db: Session = Depends(get_session)) -> list[CategoryCount]:
    """Return dispute counts grouped by reason_category, ordered descending."""
    rows = db.execute(
        select(
            McpScoreDispute.reason_category,
            func.count(McpScoreDispute.id).label("count"),
        )
        .group_by(McpScoreDispute.reason_category)
        .order_by(func.count(McpScoreDispute.id).desc())
        .limit(20)
    ).all()
    return [CategoryCount(reason_category=r[0] or "unknown", count=r[1]) for r in rows]


@router.get("/disputes/analytics/recent", response_model=list[DisputeWithServer])
def get_recent(db: Session = Depends(get_session)) -> list[DisputeWithServer]:
    """Return up to 50 most recent disputes with server names."""
    rows = db.execute(
        select(
            McpScoreDispute,
            McpServerRegistry.name.label("server_name"),
        )
        .outerjoin(
            McpServerRegistry,
            McpScoreDispute.server_id == McpServerRegistry.server_id,
        )
        .order_by(McpScoreDispute.created_at.desc())
        .limit(50)
    ).all()
    result = []
    for dispute, server_name in rows:
        entry = DisputeWithServer.model_validate(dispute)
        entry.server_name = server_name
        result.append(entry)
    return result


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
        sess.execute(text("INSERT INTO mcp_server_registry VALUES (:sid, :name)"),
                     {"sid": "srv_x", "name": "Server X"})
        sess.execute(text("INSERT INTO mcp_server_registry VALUES (:sid, :name)"),
                     {"sid": "srv_y", "name": "Server Y"})
        disputes = [
            ("srv_x", "u1", "HIGH",   "incorrect_category", "open",     now - timedelta(days=5), None),
            ("srv_x", "u2", "MEDIUM", "missing_data",        "pending",  now - timedelta(days=3), None),
            ("srv_y", "u3", "LOW",    "incorrect_category",  "resolved", now - timedelta(days=2), now - timedelta(days=1)),
            ("srv_x", "u4", "HIGH",   "outdated_score",      "open",     now - timedelta(days=1), None),
        ]
        for sid, sb, risk, cat, st, ca, ra in disputes:
            sess.execute(
                text("""
                    INSERT INTO mcp_score_disputes
                        (server_id, submitted_by, proposed_overall_risk,
                         reason_category, explanation, status, created_at, resolved_at)
                    VALUES (:sid, :sb, :risk, :cat, :exp, :st, :ca, :ra)
                """),
                {"sid": sid, "sb": sb, "risk": risk, "cat": cat,
                 "exp": "test", "st": st, "ca": ca, "ra": ra},
            )
        sess.commit()

    client = TestClient(app)

    # Overview
    r = client.get("/api/disputes/analytics/overview")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total"] == 4, f"total: {d['total']}"
    assert d["open"] == 2, f"open: {d['open']}"
    assert d["pending"] == 1, f"pending: {d['pending']}"
    assert d["resolved"] == 1, f"resolved: {d['resolved']}"
    assert d["resolution_rate"] == 25.0, f"resolution_rate: {d['resolution_rate']}"
    assert d["avg_resolution_days"] == 1.0, f"avg_resolution_days: {d['avg_resolution_days']}"

    # By-category
    r2 = client.get("/api/disputes/analytics/by-category")
    assert r2.status_code == 200, r2.text
    cats = r2.json()
    assert len(cats) >= 1, f"expected >=1 categories, got {len(cats)}"
    assert cats[0]["reason_category"] == "incorrect_category", f"top cat: {cats[0]}"
    assert cats[0]["count"] == 2, f"incorrect_category count: {cats[0]}"

    # Recent
    r3 = client.get("/api/disputes/analytics/recent")
    assert r3.status_code == 200, r3.text
    recent = r3.json()
    assert len(recent) == 4, f"expected 4, got {len(recent)}"
    assert recent[0]["server_name"] == "Server X", f"first: {recent[0]}"

    print("PASS")
