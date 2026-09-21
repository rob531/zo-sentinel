from datetime import datetime, timedelta
from typing import Dict, List

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class AxisDetail(BaseModel):
    label: str
    p_top: float | None = None
    p_critical: float | None = None


class SeriesItem(BaseModel):
    scored_at: datetime
    axes: Dict[str, AxisDetail]


class TimelineResponse(BaseModel):
    server_id: int
    server_name: str
    days: int
    series: List[SeriesItem]


def get_score_timeline(
    server_id: int,
    days: int = 7,
    db: Session = Depends(get_session),
) -> TimelineResponse:
    """Return a timeline of LLM axis scores for a given server."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    rows = (
        db.query(McpLlmAxisScore)
        .join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.id,
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.id == server_id)
        .first()
    )
    server_name = getattr(server, "server_name", "") if server else ""

    # Group rows by timestamp
    grouped: Dict[datetime, Dict[str, AxisDetail]] = {}
    for row in rows:
        ts = row.scored_at
        if ts not in grouped:
            grouped[ts] = {}
        grouped[ts][row.axis_name] = AxisDetail(
            label=row.axis_name,
            p_top=row.p_top,
            p_critical=row.p_critical,
        )

    # Build chronological series (oldest first)
    series: List[SeriesItem] = [
        SeriesItem(scored_at=ts, axes=axes) for ts, axes in sorted(grouped.items())
    ]

    return TimelineResponse(
        server_id=server_id,
        server_name=server_name,
        days=days,
        series=series,
    )


if __name__ == "__main__":
    # Self‑test using an in‑memory SQLite DB
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    # Seed two servers
    server1 = McpServerRegistry(id=1, server_name="ServerOne")
    server2 = McpServerRegistry(id=2, server_name="ServerTwo")
    db.add_all([server1, server2])

    # Axis names expected by the service
    axis_names = [
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    ]

    now = datetime.utcnow()
    snapshots = [
        now - timedelta(hours=1),
        now - timedelta(days=1, hours=2),
        now - timedelta(days=1, hours=5),
    ]

    # Create three snapshots for each server
    for srv_id in (1, 2):
        for ts in snapshots:
            for axis in axis_names:
                score = McpLlmAxisScore(
                    server_id=srv_id,
                    scored_at=ts,
                    axis_name=axis,
                    p_top=0.5,
                    p_critical=0.2,
                )
                db.add(score)

    db.commit()

    # Run the logic
    resp = get_score_timeline(server_id=1, days=2, db=db)

    assert resp.server_id == 1
    assert len(resp.series) >= 2
    for item in resp.series:
        assert "overall_risk" in item.axes

    print("PASS")