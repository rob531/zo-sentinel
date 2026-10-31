from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Query
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


TIER_ORDER = {
    "TRUSTED_GENERAL": 6,
    "TRUSTED_RESEARCH": 5,
    "ENTERPRISE_CONTROLLED": 4,
    "CAUTION_LIMITED": 3,
    "HIGH_RISK_ISOLATED": 2,
    "KNOWN_THREAT": 1,
}


def _tier_shift(proposed_tier: Optional[str], current_tier: Optional[str]) -> str:
    if proposed_tier not in TIER_ORDER or current_tier not in TIER_ORDER:
        return "unknown"
    if proposed_tier == current_tier:
        return "same"
    if TIER_ORDER[proposed_tier] > TIER_ORDER[current_tier]:
        return "upgrade"
    return "downgrade"


def _decode_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return value
    return value


def query_dispute_queue(
    session: Session,
    status: Optional[str] = "open",
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    limit = min(max(int(limit), 1), 200)
    offset = max(int(offset), 0)

    where_sql = ""
    params: Dict[str, Any] = {"limit": limit, "offset": offset}
    if status is not None:
        where_sql = " WHERE d.status = :status"
        params["status"] = status

    total_row = session.execute(
        text(
            "SELECT COUNT(*) AS total "
            "FROM mcp_score_disputes d"
            + where_sql
        ),
        params,
    ).mappings().first()
    total = int(total_row["total"] if total_row else 0)

    rows = session.execute(
        text(
            """
            SELECT
                d.id,
                d.server_id,
                d.submitted_by,
                d.proposed_overall_risk,
                d.proposed_axes,
                d.reason_category,
                d.explanation,
                d.status,
                d.admin_note,
                d.created_at,
                d.resolved_at,
                r.name AS server_name,
                r.risk_tier AS current_tier,
                r.verdict AS current_verdict,
                r.trust_score AS current_trust_score
            FROM mcp_score_disputes d
            LEFT JOIN mcp_server_registry r
                ON r.server_id = d.server_id
            """
            + where_sql
            + " ORDER BY d.created_at DESC"
            " LIMIT :limit OFFSET :offset"
        ),
        params,
    ).mappings().all()

    items: List[Dict[str, Any]] = []
    for row in rows:
        current_tier = row["current_tier"]
        proposed_tier = row["proposed_overall_risk"]
        items.append(
            {
                "id": row["id"],
                "server_id": row["server_id"],
                "server_name": row["server_name"],
                "submitted_by": row["submitted_by"],
                "reason_category": row["reason_category"],
                "status": row["status"],
                "created_at": row["created_at"],
                "resolved_at": row["resolved_at"],
                "current_tier": current_tier,
                "current_verdict": row["current_verdict"],
                "proposed_tier": proposed_tier,
                "tier_shift": _tier_shift(proposed_tier, current_tier),
                "proposed_axes": _decode_json(row["proposed_axes"]),
            }
        )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "status_filter": status,
        "items": items,
    }


def get_dispute_queue(
    status: Optional[str] = Query("open"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return query_dispute_queue(
        session=session,
        status=status,
        limit=limit,
        offset=offset,
    )


def _create_test_database() -> Engine:
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR NOT NULL,
                    submitted_by VARCHAR NOT NULL,
                    proposed_overall_risk VARCHAR NOT NULL,
                    proposed_axes JSON,
                    reason_category VARCHAR NOT NULL,
                    explanation TEXT NOT NULL,
                    status VARCHAR NOT NULL DEFAULT 'open',
                    admin_note TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    resolved_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    risk_tier VARCHAR,
                    verdict VARCHAR,
                    trust_score FLOAT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, risk_tier, verdict, trust_score)
                VALUES
                    ('server-upgrade', 'Upgrade Server', 'HIGH_RISK_ISOLATED', 'quarantined', 22.0),
                    ('server-downgrade', 'Downgrade Server', 'TRUSTED_GENERAL', 'approved', 96.0),
                    ('server-resolved', 'Resolved Server', 'TRUSTED_RESEARCH', 'approved', 90.0)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_score_disputes
                    (id, server_id, submitted_by, proposed_overall_risk,
                     proposed_axes, reason_category, explanation, status,
                     created_at, resolved_at)
                VALUES
                    (1, 'server-upgrade', 'analyst-a', 'TRUSTED_GENERAL',
                     '["security", "accuracy"]', 'false_positive',
                     'The server was incorrectly classified.', 'open',
                     '2024-01-03 12:00:00', NULL),
                    (2, 'server-downgrade', 'analyst-b', 'HIGH_RISK_ISOLATED',
                     '["security"]', 'new_evidence',
                     'New evidence shows elevated risk.', 'open',
                     '2024-01-02 12:00:00', NULL),
                    (3, 'server-resolved', 'analyst-c', 'TRUSTED_GENERAL',
                     '["accuracy"]', 'correction',
                     'The score was corrected.', 'resolved',
                     '2024-01-01 12:00:00', '2024-01-01 13:00:00')
                """
            )
        )
    return engine


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    engine = _create_test_database()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    test_session = session_factory()

    app = FastAPI()

    @app.get("/api/disputes/queue")
    def dispute_queue(
        status: Optional[str] = Query("open"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        session: Session = Depends(get_session),
    ):
        return query_dispute_queue(session, status, limit, offset)

    def override_get_session():
        yield test_session

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        response = client.get("/api/disputes/queue", params={"status": "open"})
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["items"]) == 2
        assert payload["total"] == 2
        assert "items" in payload and "total" in payload

        upgrade_item = next(
            item for item in payload["items"] if item["server_id"] == "server-upgrade"
        )
        assert upgrade_item["tier_shift"] == "upgrade"
        assert upgrade_item["current_tier"] == "HIGH_RISK_ISOLATED"

    test_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()