from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


_DISPUTE_TABLE = McpScoreDispute.__tablename__
_REGISTRY_TABLE = McpServerRegistry.__tablename__

_DISPUTE_QUERY = f"""
SELECT
    d.id,
    d.server_id,
    d.proposed_overall_risk,
    r.server_id AS registry_server_id,
    r.risk_tier AS current_tier,
    d.status,
    d.reason_category,
    d.created_at
FROM {_DISPUTE_TABLE} AS d
LEFT JOIN {_REGISTRY_TABLE} AS r
    ON r.server_id = d.server_id
ORDER BY d.created_at DESC, d.id DESC
"""


def get_dispute_tier_disagreement(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    rows = session.execute(text(_DISPUTE_QUERY)).mappings().all()

    total = len(rows)
    status_counts: Counter[Any] = Counter()
    classification_counts = {
        "agrees": 0,
        "disagrees": 0,
        "unknown_tier": 0,
        "unset": 0,
    }
    unresolved_disagreement_count = 0
    samples: list[dict[str, Any]] = []

    for row in rows:
        status = row["status"]
        status_counts[status if status is not None else "unknown"] += 1

        proposed_tier = row["proposed_overall_risk"]
        current_tier = row["current_tier"]
        registry_row_exists = row["registry_server_id"] is not None

        if proposed_tier is None:
            classification = "unset"
        elif not registry_row_exists:
            classification = "unknown_tier"
        elif str(proposed_tier).casefold() == str(current_tier).casefold():
            classification = "agrees"
        else:
            classification = "disagrees"

        classification_counts[classification] += 1

        if (
            classification == "disagrees"
            and (status is None or str(status).casefold() != "resolved")
        ):
            unresolved_disagreement_count += 1

        samples.append(
            {
                "id": row["id"],
                "server_id": row["server_id"],
                "proposed_overall_risk": proposed_tier,
                "current_tier": current_tier,
                "status": status,
                "reason_category": row["reason_category"],
            }
        )

    disagreement_rate = (
        round(classification_counts["disagrees"] / total, 4) if total else 0.0
    )

    return {
        "total": total,
        "by_status": dict(status_counts),
        "by_classification": classification_counts,
        "disagreement_rate": disagreement_rate,
        "unresolved_disagreement_count": unresolved_disagreement_count,
        "samples": samples[:25],
    }


get_dispute_tier_disagreement_data = get_dispute_tier_disagreement
get_dispute_tier_disagreement_stats = get_dispute_tier_disagreement
get_tier_disagreement_report = get_dispute_tier_disagreement
get_tier_disagreement_stats = get_dispute_tier_disagreement
get_stats = get_dispute_tier_disagreement


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    risk_tier TEXT,
                    verdict TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    submitted_by TEXT,
                    proposed_overall_risk TEXT,
                    proposed_axes TEXT,
                    reason_category TEXT,
                    explanation TEXT,
                    status TEXT,
                    admin_note TEXT,
                    created_at TIMESTAMP,
                    resolved_at TIMESTAMP
                )
                """
            )
        )

    with TestSession() as session:
        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, risk_tier, verdict)
                VALUES (:server_id, :risk_tier, :verdict)
                """
            ),
            [
                {
                    "server_id": "server-agree",
                    "risk_tier": "Medium",
                    "verdict": "allow",
                },
                {
                    "server_id": "server-disagree",
                    "risk_tier": "High",
                    "verdict": "block",
                },
                {
                    "server_id": "server-unset",
                    "risk_tier": "Low",
                    "verdict": "allow",
                },
            ],
        )
        session.execute(
            text(
                """
                INSERT INTO mcp_score_disputes (
                    id,
                    server_id,
                    submitted_by,
                    proposed_overall_risk,
                    proposed_axes,
                    reason_category,
                    explanation,
                    status,
                    admin_note,
                    created_at,
                    resolved_at
                )
                VALUES (
                    :id,
                    :server_id,
                    :submitted_by,
                    :proposed_overall_risk,
                    :proposed_axes,
                    :reason_category,
                    :explanation,
                    :status,
                    :admin_note,
                    :created_at,
                    :resolved_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "server_id": "server-agree",
                    "submitted_by": "user-1",
                    "proposed_overall_risk": "medium",
                    "proposed_axes": "[]",
                    "reason_category": "tier_review",
                    "explanation": "Matching tier",
                    "status": "open",
                    "admin_note": None,
                    "created_at": "2025-01-04T00:00:00",
                    "resolved_at": None,
                },
                {
                    "id": 2,
                    "server_id": "server-disagree",
                    "submitted_by": "user-2",
                    "proposed_overall_risk": "low",
                    "proposed_axes": "[]",
                    "reason_category": "tier_review",
                    "explanation": "Different tier",
                    "status": "open",
                    "admin_note": None,
                    "created_at": "2025-01-03T00:00:00",
                    "resolved_at": None,
                },
                {
                    "id": 3,
                    "server_id": "server-missing",
                    "submitted_by": "user-3",
                    "proposed_overall_risk": "critical",
                    "proposed_axes": "[]",
                    "reason_category": "tier_review",
                    "explanation": "Registry row is absent",
                    "status": "pending",
                    "admin_note": None,
                    "created_at": "2025-01-02T00:00:00",
                    "resolved_at": None,
                },
                {
                    "id": 4,
                    "server_id": "server-unset",
                    "submitted_by": "user-4",
                    "proposed_overall_risk": None,
                    "proposed_axes": "[]",
                    "reason_category": "tier_review",
                    "explanation": "No proposed tier",
                    "status": "resolved",
                    "admin_note": None,
                    "created_at": "2025-01-01T00:00:00",
                    "resolved_at": "2025-01-05T00:00:00",
                },
            ],
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()

    @that_app.get("/api/disputes/tier-disagreement")
    def tier_disagreement_endpoint(
        result: dict[str, Any] = Depends(get_dispute_tier_disagreement),
    ) -> dict[str, Any]:
        return result

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/tier-disagreement")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["by_classification"] == {
        "agrees": 1,
        "disagrees": 1,
        "unknown_tier": 1,
        "unset": 1,
    }
    assert payload["disagreement_rate"] == 0.25
    assert payload["unresolved_disagreement_count"] == 1
    assert len(payload["samples"]) <= 25

    print("PASS")