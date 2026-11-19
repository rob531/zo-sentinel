"""logic.py -- axis freshness SLA computation over the real application data layer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


VALID_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_axis_freshness(db: Session, sla_hours: int = 24) -> Dict[str, Any]:
    """Compute score freshness, stale rows, and never-scored servers per axis."""
    hours = max(0, int(sla_hours))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    score_rows = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.scored_at,
        ).where(McpLlmAxisScore.axis_name.in_(VALID_AXES))
    ).all()
    registry_ids = set(
        db.execute(select(McpServerRegistry.server_id)).scalars().all()
    )

    grouped: Dict[str, List[Any]] = {axis: [] for axis in VALID_AXES}
    for axis_name, server_id, scored_at in score_rows:
        if axis_name in grouped:
            grouped[axis_name].append((server_id, scored_at))

    axes: List[Dict[str, Any]] = []
    stale_axes: List[str] = []

    for axis_name in VALID_AXES:
        rows = grouped[axis_name]
        timestamps = [
            _utc(scored_at)
            for _, scored_at in rows
            if scored_at is not None
        ]
        last_scored_at = max(timestamps) if timestamps else None
        stale_count = sum(
            1 for scored_at in timestamps if scored_at < cutoff
        )
        scored_servers = {
            server_id for server_id, _ in rows if server_id is not None
        }
        never_scored = len(registry_ids - scored_servers)

        if stale_count:
            stale_axes.append(axis_name)

        if last_scored_at is None:
            age_hours = 0.0
        else:
            age_hours = max(
                0.0,
                (now - last_scored_at).total_seconds() / 3600.0,
            )

        axes.append(
            {
                "axis_name": axis_name,
                "scored_count": len(rows),
                "servers_total": len(registry_ids),
                "never_scored": never_scored,
                "stale_count": stale_count,
                "last_scored_at": last_scored_at,
                "age_hours": age_hours,
            }
        )

    return {
        "sla_hours": hours,
        "axes": axes,
        "stale_axes": stale_axes,
    }


def get_axis_freshness(db: Session, sla_hours: int = 24) -> Dict[str, Any]:
    """Compatibility wrapper for consumers using the resource-style name."""
    return compute_axis_freshness(db, sla_hours)


def axis_freshness(db: Session, sla_hours: int = 24) -> Dict[str, Any]:
    """Compatibility wrapper for consumers using the short resource name."""
    return compute_axis_freshness(db, sla_hours)


def compute_freshness_sla(db: Session, sla_hours: int = 24) -> Dict[str, Any]:
    """Compatibility wrapper for consumers using the SLA name."""
    return compute_axis_freshness(db, sla_hours)


def get_axis_freshness_sla(
    db: Session, sla_hours: int = 24
) -> Dict[str, Any]:
    """Compatibility wrapper for consumers using the SLA resource name."""
    return compute_axis_freshness(db, sla_hours)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    local_app = FastAPI()

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    local_app.dependency_overrides[get_session] = override_get_session

    session = SessionLocal()
    try:
        session.add_all(
            [
                McpServerRegistry(server_id="server-1", name="server-1"),
                McpServerRegistry(server_id="server-2", name="server-2"),
            ]
        )
        session.flush()
        now = datetime.now(timezone.utc)
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-1",
                    axis_name="overall_risk",
                    model_version="test",
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-1",
                    axis_name="auth_strength",
                    model_version="test",
                    scored_at=now - timedelta(hours=48),
                ),
            ]
        )
        session.commit()

        result = compute_axis_freshness(session, 24)
        by_name = {axis["axis_name"]: axis for axis in result["axes"]}
        assert by_name["overall_risk"]["never_scored"] >= 1
        assert "auth_strength" in result["stale_axes"]
        assert "overall_risk" not in result["stale_axes"]
        assert all(axis["age_hours"] >= 0 for axis in result["axes"])
    finally:
        session.close()
        engine.dispose()

    print("PASS")