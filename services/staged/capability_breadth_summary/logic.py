from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


class SummaryResponse(BaseModel):
    axis: str
    summary: dict[str, Any]


def compute_capability_breadth_summary(session: Session) -> dict[str, Any]:
    """Compute capability breadth summary from real axis scores."""
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)

    # Base query for capability_breadth scores
    base_filter = McpLlmAxisScore.axis_name == "capability_breadth"

    # Get all scores for aggregates
    scores_query = select(McpLlmAxisScore).where(base_filter)
    scores = session.execute(scores_query).scalars().all()

    if not scores:
        return {
            "axis": "capability_breadth",
            "summary": {
                "avg_p_top": 0.0,
                "avg_p_critical": 0.0,
                "tier_counts": {},
                "trend_series": [],
            },
        }

    # Compute axis-level aggregates
    avg_p_top = sum(s.p_top for s in scores if s.p_top is not None) / len(scores)
    avg_p_critical = sum(s.p_critical for s in scores if s.p_critical is not None) / len(scores)

    # Count by label_index
    label_index_counts = {}
    for s in scores:
        idx = s.label_index
        label_index_counts[idx] = label_index_counts.get(idx, 0) + 1

    # Compute tier counts based on p_top ranges
    tier_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for s in scores:
        p = s.p_top or 0.0
        if p >= 0.8:
            tier_counts["critical"] += 1
        elif p >= 0.6:
            tier_counts["high"] += 1
        elif p >= 0.4:
            tier_counts["medium"] += 1
        else:
            tier_counts["low"] += 1

    # Compute trend series over last 30 days
    trend_query = (
        select(
            func.date(McpLlmAxisScore.scored_at).label("date"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
        )
        .where(base_filter)
        .where(McpLlmAxisScore.scored_at >= thirty_days_ago)
        .group_by(func.date(McpLlmAxisScore.scored_at))
        .order_by(func.date(McpLlmAxisScore.scored_at))
    )
    trend_results = session.execute(trend_query).all()

    trend_series = [
        {"date": str(r.date), "avg_p_top": float(r.avg_p_top)} for r in trend_results
    ]

    return {
        "axis": "capability_breadth",
        "summary": {
            "avg_p_top": float(avg_p_top),
            "avg_p_critical": float(avg_p_critical),
            "tier_counts": tier_counts,
            "trend_series": trend_series,
        },
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import StaticPool, create_engine
    from sqlalchemy.orm import sessionmaker

    # In-memory self-test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base

    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()

    # Seed 5 servers with capability_breadth scores across 2 label indices
    from app.models import McpLlmAxisScore
    from datetime import datetime, timedelta

    servers_data = [
        {"server_id": "srv1", "label_index": 0, "p_top": 0.9, "p_critical": 0.05, "scored_at": datetime.utcnow() - timedelta(days=1)},
        {"server_id": "srv2", "label_index": 0, "p_top": 0.7, "p_critical": 0.10, "scored_at": datetime.utcnow() - timedelta(days=2)},
        {"server_id": "srv3", "label_index": 1, "p_top": 0.5, "p_critical": 0.20, "scored_at": datetime.utcnow() - timedelta(days=3)},
        {"server_id": "srv4", "label_index": 1, "p_top": 0.3, "p_critical": 0.30, "scored_at": datetime.utcnow() - timedelta(days=4)},
        {"server_id": "srv5", "label_index": 0, "p_top": 0.85, "p_critical": 0.08, "scored_at": datetime.utcnow() - timedelta(days=5)},
    ]

    for i, data in enumerate(servers_data, 1):
        score = McpLlmAxisScore(
            id=i,
            server_id=data["server_id"],
            axis_name="capability_breadth",
            label_index=data["label_index"],
            p_top=data["p_top"],
            p_critical=data["p_critical"],
            scored_at=data["scored_at"],
            label="test",
            adapter_sha256="abc",
            model_version="v1",
            decision_rule_version="r1",
        )
        session.add(score)
    session.commit()

    result = compute_capability_breadth_summary(session)

    # Assertions
    assert isinstance(result["summary"]["avg_p_top"], float), f"avg_p_top type: {type(result['summary']['avg_p_top'])}"
    assert "tier_counts" in result["summary"]
    assert isinstance(result["summary"]["tier_counts"], dict)
    assert result["axis"] == "capability_breadth"
    assert "critical" in result["summary"]["tier_counts"]
    assert "high" in result["summary"]["tier_counts"]
    assert "medium" in result["summary"]["tier_counts"]
    assert "low" in result["summary"]["tier_counts"]

    print("PASS")