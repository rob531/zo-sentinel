"""Logic for the dispute submitter concentration service."""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def get_submitter_concentration(db: Session) -> Dict[str, Any]:
    """Return dispute counts, server coverage, open counts, and top reasons."""
    submitter_rows = db.execute(
        select(
            McpScoreDispute.submitted_by.label("submitted_by"),
            func.count(McpScoreDispute.id).label("disputes"),
            func.count(distinct(McpScoreDispute.server_id)).label("servers"),
            func.coalesce(
                func.sum(
                    case(
                        (McpScoreDispute.status != "resolved", 1),
                        else_=0,
                    )
                ),
                0,
            ).label("open"),
        )
        .group_by(McpScoreDispute.submitted_by)
        .order_by(
            func.count(McpScoreDispute.id).desc(),
            McpScoreDispute.submitted_by.asc(),
        )
    ).all()

    reason_counts = (
        select(
            McpScoreDispute.submitted_by.label("submitted_by"),
            McpScoreDispute.reason_category.label("reason_category"),
            func.count(McpScoreDispute.id).label("reason_count"),
            func.row_number()
            .over(
                partition_by=McpScoreDispute.submitted_by,
                order_by=(
                    func.count(McpScoreDispute.id).desc(),
                    McpScoreDispute.reason_category.asc(),
                ),
            )
            .label("reason_rank"),
        )
        .group_by(
            McpScoreDispute.submitted_by,
            McpScoreDispute.reason_category,
        )
        .subquery()
    )

    top_reason_rows = db.execute(
        select(
            reason_counts.c.submitted_by,
            reason_counts.c.reason_category,
        ).where(reason_counts.c.reason_rank == 1)
    ).all()

    top_reasons = {
        submitted_by: reason_category
        for submitted_by, reason_category in top_reason_rows
    }

    submitters: List[Dict[str, Any]] = []
    for row in submitter_rows:
        submitters.append(
            {
                "submitted_by": row.submitted_by,
                "disputes": int(row.disputes),
                "servers": int(row.servers),
                "open": int(row.open or 0),
                "top_reason_category": top_reasons.get(row.submitted_by),
            }
        )

    totals = db.execute(
        select(
            func.count(McpScoreDispute.id).label("total_disputes"),
            func.coalesce(
                func.sum(
                    case(
                        (McpScoreDispute.status != "resolved", 1),
                        else_=0,
                    )
                ),
                0,
            ).label("open_disputes"),
        )
    ).one()

    return {
        "submitters": submitters,
        "total_disputes": int(totals.total_disputes or 0),
        "open_disputes": int(totals.open_disputes or 0),
    }


def compute_submitter_concentration(db: Session) -> Dict[str, Any]:
    """Compatibility alias for the service's computation entry point."""
    return get_submitter_concentration(db)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    that_session = session_factory()
    that_session.add_all(
        [
            McpScoreDispute(
                id=1,
                server_id="server-a",
                submitted_by="alice",
                proposed_overall_risk=0.8,
                proposed_axes=None,
                reason_category="data_quality",
                explanation="First review",
                status="open",
            ),
            McpScoreDispute(
                id=2,
                server_id="server-a",
                submitted_by="alice",
                proposed_overall_risk=0.7,
                proposed_axes=None,
                reason_category="data_quality",
                explanation="Second review",
                status="open",
            ),
            McpScoreDispute(
                id=3,
                server_id="server-b",
                submitted_by="alice",
                proposed_overall_risk=0.6,
                proposed_axes=None,
                reason_category="model_disagreement",
                explanation="Third review",
                status="resolved",
            ),
            McpScoreDispute(
                id=4,
                server_id="server-b",
                submitted_by="bob",
                proposed_overall_risk=0.4,
                proposed_axes=None,
                reason_category="model_disagreement",
                explanation="Fourth review",
                status="open",
            ),
        ]
    )
    that_session.commit()

    def override_session():
        yield that_session

    that_app = FastAPI()

    @that_app.get("/api/disputes/submitter-concentration")
    def submitter_concentration(db: Session = Depends(get_session)):
        return get_submitter_concentration(db)

    that_app.dependency_overrides[get_session] = override_session
    with TestClient(that_app) as client:
        response = client.get("/api/disputes/submitter-concentration")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_disputes"] == 4
        assert payload["open_disputes"] == 3
        assert max(item["disputes"] for item in payload["submitters"]) == 3

    print("PASS")