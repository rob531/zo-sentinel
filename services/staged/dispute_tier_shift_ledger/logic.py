from datetime import date, datetime
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


def _iso_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def get_shift_ledger(
    session: Session = Depends(get_session),
    status: Optional[str] = None,
    server_id: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    filters = []
    if status is not None:
        filters.append(McpScoreDispute.status == status)
    if server_id is not None:
        filters.append(McpScoreDispute.server_id == server_id)

    total = int(
        session.query(func.count(McpScoreDispute.id))
        .filter(*filters)
        .scalar()
        or 0
    )

    count_rows = (
        session.query(
            McpScoreDispute.status,
            func.count(McpScoreDispute.id),
        )
        .filter(*filters)
        .group_by(McpScoreDispute.status)
        .all()
    )

    known_statuses = ("pending", "resolved", "rejected")
    if status is None:
        status_counts = {key: 0 for key in known_statuses} if total else {}
    else:
        status_counts = {}

    for row_status, count in count_rows:
        if row_status in known_statuses:
            status_counts[row_status] = int(count)

    safe_limit = max(0, min(int(limit), 500))
    rows = (
        session.query(
            McpScoreDispute.server_id.label("server_id"),
            McpServerRegistry.name.label("name"),
            McpServerRegistry.risk_tier.label("current_tier"),
            McpScoreDispute.proposed_overall_risk.label(
                "proposed_overall_risk"
            ),
            McpScoreDispute.status.label("status"),
            McpScoreDispute.reason_category.label("reason_category"),
            McpScoreDispute.created_at.label("created_at"),
            McpScoreDispute.resolved_at.label("resolved_at"),
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .filter(*filters)
        .order_by(
            McpScoreDispute.created_at.desc(),
            McpScoreDispute.id.desc(),
        )
        .limit(safe_limit)
        .all()
    )

    shifts = [
        {
            "server_id": row.server_id,
            "name": row.name,
            "current_tier": row.current_tier,
            "proposed_overall_risk": row.proposed_overall_risk,
            "status": row.status,
            "reason_category": row.reason_category,
            "created_at": _iso_timestamp(row.created_at),
            "resolved_at": _iso_timestamp(row.resolved_at),
        }
        for row in rows
    ]

    return {
        "status_counts": status_counts,
        "shifts": shifts,
        "total": total,
    }


get_dispute_shift_ledger = get_shift_ledger
get_dispute_tier_shift_ledger = get_shift_ledger
get_shift_ledger_summary = get_shift_ledger
build_shift_ledger = get_shift_ledger


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    proposed_overall_risk TEXT NOT NULL,
                    reason_category TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    resolved_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    risk_tier TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                VALUES (:server_id, :name, :risk_tier)
                """
            ),
            [
                {"server_id": "server-a", "name": "Server A", "risk_tier": "high"},
                {"server_id": "server-b", "name": "Server B", "risk_tier": "low"},
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_score_disputes (
                    id,
                    server_id,
                    proposed_overall_risk,
                    reason_category,
                    status,
                    created_at,
                    resolved_at
                )
                VALUES (
                    :id,
                    :server_id,
                    :proposed_overall_risk,
                    :reason_category,
                    :status,
                    :created_at,
                    :resolved_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "server_id": "server-a",
                    "proposed_overall_risk": "medium",
                    "reason_category": "context",
                    "status": "pending",
                    "created_at": datetime(2025, 1, 3, 12, 0, 0),
                    "resolved_at": None,
                },
                {
                    "id": 2,
                    "server_id": "server-b",
                    "proposed_overall_risk": "low",
                    "reason_category": "false_positive",
                    "status": "resolved",
                    "created_at": datetime(2025, 1, 2, 12, 0, 0),
                    "resolved_at": datetime(2025, 1, 4, 12, 0, 0),
                },
                {
                    "id": 3,
                    "server_id": "server-a",
                    "proposed_overall_risk": "critical",
                    "reason_category": "signals",
                    "status": "rejected",
                    "created_at": datetime(2025, 1, 1, 12, 0, 0),
                    "resolved_at": datetime(2025, 1, 2, 12, 0, 0),
                },
            ],
        )

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()

    @that_app.get("/api/disputes/shift-ledger")
    def shift_ledger_endpoint(
        status: Optional[str] = None,
        server_id: Optional[str] = None,
        limit: int = 100,
        session: Session = Depends(get_session),
    ):
        return get_shift_ledger(
            session=session,
            status=status,
            server_id=server_id,
            limit=limit,
        )

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/shift-ledger")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert any(
            shift["current_tier"] == "high" for shift in payload["shifts"]
        )

        filtered = client.get(
            "/api/disputes/shift-ledger",
            params={"status": "pending"},
        )
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert filtered_payload["status_counts"] == {"pending": 1}
        assert filtered_payload["total"] == 1

    print("PASS")