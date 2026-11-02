"""Data and computation layer for dispute submitter rankings."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import bindparam, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def get_dispute_submitter_rankings(
    db: Session, days: int = 30
) -> List[Dict[str, object]]:
    """Return dispute aggregates for each submitter within the requested window."""
    if days < 1 or days > 365:
        raise ValueError("days must be between 1 and 365")

    window_start = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(
        select(
            McpScoreDispute.submitted_by,
            McpScoreDispute.server_id,
            McpScoreDispute.reason_category,
            McpScoreDispute.status,
            McpScoreDispute.resolved_at,
        )
        .where(
            McpScoreDispute.created_at >= bindparam("window_start", window_start)
        )
        .order_by(McpScoreDispute.created_at.desc())
    ).all()

    aggregates: Dict[str, Dict[str, object]] = {}
    for submitted_by, server_id, reason_category, status, resolved_at in rows:
        name = submitted_by or "unknown"
        aggregate = aggregates.setdefault(
            name,
            {
                "submitted_by": name,
                "disputes_submitted": 0,
                "disputes_resolved": 0,
                "distinct_servers": set(),
                "reason_counts": Counter(),
            },
        )
        aggregate["disputes_submitted"] = int(aggregate["disputes_submitted"]) + 1
        if (status or "").lower() == "resolved" or resolved_at is not None:
            aggregate["disputes_resolved"] = int(aggregate["disputes_resolved"]) + 1
        aggregate["distinct_servers"].add(server_id)
        if reason_category:
            aggregate["reason_counts"][reason_category] += 1

    output: List[Dict[str, object]] = []
    for aggregate in aggregates.values():
        reason_counts = aggregate["reason_counts"]
        top_reason_category = None
        if reason_counts:
            top_reason_category = min(
                reason_counts,
                key=lambda reason: (-reason_counts[reason], reason),
            )
        submitted = int(aggregate["disputes_submitted"])
        resolved = int(aggregate["disputes_resolved"])
        output.append(
            {
                "submitted_by": aggregate["submitted_by"],
                "disputes_submitted": submitted,
                "disputes_resolved": resolved,
                "resolution_rate": resolved / submitted if submitted else 0.0,
                "distinct_servers": len(aggregate["distinct_servers"]),
                "top_reason_category": top_reason_category,
            }
        )

    output.sort(
        key=lambda item: (-int(item["disputes_submitted"]), str(item["submitted_by"]))
    )
    return output


get_submitter_rankings = get_dispute_submitter_rankings
get_submitter_aggregates = get_dispute_submitter_rankings
get_dispute_submitter_aggregates = get_dispute_submitter_rankings
get_submitter_stats = get_dispute_submitter_rankings
get_ranked_submitters = get_dispute_submitter_rankings


if __name__ == "__main__":
    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    with TestingSessionLocal() as session:
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id="server-1",
                    submitted_by="resolved-submitter",
                    proposed_overall_risk=0.8,
                    reason_category="policy",
                    status="resolved",
                    explanation="First resolved dispute",
                    created_at=now - timedelta(days=1),
                    resolved_at=now - timedelta(days=1),
                ),
                McpScoreDispute(
                    id=2,
                    server_id="server-2",
                    submitted_by="resolved-submitter",
                    proposed_overall_risk=0.6,
                    reason_category="quality",
                    status="resolved",
                    explanation="Second resolved dispute",
                    created_at=now - timedelta(days=2),
                    resolved_at=now - timedelta(days=2),
                ),
                McpScoreDispute(
                    id=3,
                    server_id="server-1",
                    submitted_by="unresolved-submitter",
                    proposed_overall_risk=0.7,
                    reason_category="quality",
                    status="open",
                    explanation="First unresolved dispute",
                    created_at=now - timedelta(days=3),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    id=4,
                    server_id="server-2",
                    submitted_by="unresolved-submitter",
                    proposed_overall_risk=0.5,
                    reason_category="network",
                    status="open",
                    explanation="Second unresolved dispute",
                    created_at=now - timedelta(days=4),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    id=5,
                    server_id="server-3",
                    submitted_by="past-submitter",
                    proposed_overall_risk=0.4,
                    reason_category="policy",
                    status="resolved",
                    explanation="Dispute outside the requested window",
                    created_at=now - timedelta(days=400),
                    resolved_at=now - timedelta(days=400),
                ),
            ]
        )
        session.commit()

    local_app = FastAPI()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/disputes/submitters")
    def dispute_submitters(
        days: int = Query(30, ge=1, le=365),
        db: Session = Depends(get_session),
    ):
        return {
            "days": days,
            "submitters": get_dispute_submitter_rankings(db, days),
        }

    with TestClient(local_app) as client:
        response = client.get("/api/disputes/submitters", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        submitters = payload["submitters"]
        assert len(submitters) == 2
        assert all(item["submitted_by"] != "past-submitter" for item in submitters)
        resolved = next(
            item
            for item in submitters
            if item["submitted_by"] == "resolved-submitter"
        )
        assert resolved["disputes_submitted"] == 2
        assert resolved["disputes_resolved"] == 2
        assert resolved["resolution_rate"] == 1.0
        assert resolved["distinct_servers"] == 2
        assert resolved["top_reason_category"] == "policy"

    print("PASS")