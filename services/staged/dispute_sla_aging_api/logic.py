"""Logic layer for the dispute SLA ageing service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpScoreDispute, McpServerRegistry


def _as_datetime(value: Any) -> datetime:
    """Convert a database timestamp to a comparable datetime."""
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        result = datetime.fromisoformat(text)
    else:
        result = datetime.now(timezone.utc)

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _isoformat(value: Any) -> str:
    result = _as_datetime(value)
    return result.isoformat()


def _bucket(age_days: float) -> str:
    if age_days < 1:
        return "lt_24h"
    if age_days <= 7:
        return "d1_7"
    if age_days <= 30:
        return "d8_30"
    return "gt_30d"


def get_dispute_sla_aging(
    db: Session,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Return dispute SLA ageing statistics and a paginated list of disputes."""
    limit = max(0, min(int(limit), 200))
    offset = max(0, int(offset))

    query = (
        select(
            McpScoreDispute.id,
            McpScoreDispute.server_id,
            McpServerRegistry.name,
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.created_at,
        )
        .select_from(McpScoreDispute)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .order_by(McpScoreDispute.created_at.asc(), McpScoreDispute.id.asc())
    )
    if status is not None:
        query = query.where(McpScoreDispute.status == status)

    rows = db.execute(query).all()

    by_status: Dict[str, int] = {}
    by_reason_category: Dict[str, int] = {}
    for row in rows:
        row_status = str(row.status or "unknown")
        reason = str(row.reason_category or "unknown")
        by_status[row_status] = by_status.get(row_status, 0) + 1
        by_reason_category[reason] = by_reason_category.get(reason, 0) + 1

    created_values = [_as_datetime(row.created_at) for row in rows]
    newest = max(created_values) if created_values else None
    ages: List[float] = []
    age_by_id: Dict[int, float] = {}

    if newest is not None:
        for row, created in zip(rows, created_values):
            age = max(0.0, (newest - created).total_seconds() / 86400.0)
            age_by_id[int(row.id)] = age
            ages.append(age)

    age_buckets = {
        "lt_24h": 0,
        "d1_7": 0,
        "d8_30": 0,
        "gt_30d": 0,
    }
    for age in ages:
        age_buckets[_bucket(age)] += 1

    unresolved_ages = [
        age
        for row, age in zip(rows, ages)
        if str(row.status or "").lower() != "resolved"
    ]
    avg_age_days = (
        sum(unresolved_ages) / len(unresolved_ages)
        if unresolved_ages
        else 0.0
    )
    max_age_days = int(max(unresolved_ages)) if unresolved_ages else 0
    oldest_unresolved_days = max_age_days

    items = []
    for row in rows:
        age = age_by_id.get(int(row.id), 0.0)
        items.append(
            {
                "id": int(row.id),
                "server_id": str(row.server_id),
                "server_name": row.name,
                "status": str(row.status or "unknown"),
                "reason_category": str(row.reason_category or "unknown"),
                "proposed_overall_risk": row.proposed_overall_risk,
                "created_at": _isoformat(row.created_at),
                "age_days": age,
                "bucket": _bucket(age),
            }
        )

    return {
        "total": len(rows),
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "age_buckets": age_buckets,
        "avg_age_days": avg_age_days,
        "max_age_days": max_age_days,
        "oldest_unresolved_days": oldest_unresolved_days,
        "items": items[offset : offset + limit],
    }


def get_sla_aging(
    db: Session,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Compatibility alias for callers using the shorter function name."""
    return get_dispute_sla_aging(db, status=status, limit=limit, offset=offset)


def fetch_dispute_sla_aging(
    db: Session,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Compatibility alias for callers using the fetch-style function name."""
    return get_dispute_sla_aging(db, status=status, limit=limit, offset=offset)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    McpScoreDispute.__table__.create(engine)
    McpServerRegistry.__table__.create(engine)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="srv-a", name="Server A", risk_tier="low"),
                McpServerRegistry(server_id="srv-b", name="Server B", risk_tier="high"),
            ]
        )
        session.add_all(
            [
                McpScoreDispute(
                    server_id="srv-a",
                    submitted_by="admin-a",
                    proposed_overall_risk="high",
                    reason_category="manual_review",
                    status="resolved",
                    created_at=now - timezone.utc.utcoffset(now),
                    explanation="resolved",
                ),
                McpScoreDispute(
                    server_id="srv-a",
                    submitted_by="admin-b",
                    proposed_overall_risk="medium",
                    reason_category="false_positive",
                    status="open",
                    created_at=now - timezone.utc.utcoffset(now),
                    explanation="open",
                ),
                McpScoreDispute(
                    server_id="srv-b",
                    submitted_by="admin-c",
                    proposed_overall_risk="low",
                    reason_category="appeal",
                    status="open",
                    created_at=now - timezone.utc.utcoffset(now),
                    explanation="open",
                ),
                McpScoreDispute(
                    server_id="missing-server",
                    submitted_by="admin-d",
                    proposed_overall_risk="low",
                    reason_category="other",
                    status="open",
                    created_at=now - timezone.utc.utcoffset(now),
                    explanation="orphan",
                ),
            ]
        )
        session.commit()

    app = FastAPI()

    @app.get("/api/disputes/sla-aging")
    def dispute_sla_aging() -> Dict[str, Any]:
        with TestingSession() as session:
            return get_dispute_sla_aging(session)

    def override_get_session():
        with TestingSession() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    response = TestClient(app).get("/api/disputes/sla-aging")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["by_status"]["open"] == 3
    assert payload["by_status"]["resolved"] == 1
    assert payload["items"][-1]["server_name"] is None
    print("PASS")