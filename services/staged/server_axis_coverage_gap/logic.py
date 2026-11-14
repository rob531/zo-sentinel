"""Coverage-gap computation over the real application registry and axis scores."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


COVERAGE_AXES = (
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    normalized = _utc(value)
    return normalized.isoformat() if normalized is not None else None


def compute_coverage_gap(db_session: Session, stale_days: int = 30) -> Dict:
    """Return registered servers with never-scored or stale coverage axes."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(0, int(stale_days)))

    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.scored_at,
        )
        .outerjoin(
            McpLlmAxisScore,
            and_(
                McpLlmAxisScore.server_id == McpServerRegistry.server_id,
                McpLlmAxisScore.axis_name.in_(COVERAGE_AXES),
            ),
        )
    )

    rows = db_session.execute(statement).all()
    servers: Dict[str, Dict] = {}

    for server_id, name, registry_source, risk_tier, axis_name, scored_at in rows:
        key = str(server_id)
        server = servers.setdefault(
            key,
            {
                "server_id": key,
                "name": str(name) if name is not None else "",
                "registry_source": registry_source,
                "risk_tier": risk_tier,
                "scores": {},
            },
        )

        if axis_name is not None:
            previous = server["scores"].get(axis_name)
            if previous is None or (
                previous is not None
                and previous is not None
                and (scored_at is None or previous[0] is None)
            ):
                server["scores"][axis_name] = (scored_at, False)
            elif previous is not None:
                previous_time = _utc(previous[0])
                current_time = _utc(scored_at)
                if current_time is not None and (
                    previous_time is None or current_time > previous_time
                ):
                    server["scores"][axis_name] = (scored_at, False)

    gaps = []
    for server in servers.values():
        missing_axes = []
        stale_axes = []
        score_times = []

        for axis in COVERAGE_AXES:
            score = server["scores"].get(axis)
            if score is None:
                missing_axes.append(axis)
                continue

            scored_at = score[0]
            score_times.append(scored_at)
            normalized = _utc(scored_at)
            if normalized is None or normalized < cutoff:
                stale_axes.append(axis)

        if missing_axes or stale_axes:
            valid_times = [_utc(value) for value in score_times]
            valid_times = [value for value in valid_times if value is not None]
            last_scored_at = max(valid_times) if valid_times else None
            gaps.append(
                {
                    "server_id": server["server_id"],
                    "name": server["name"],
                    "registry_source": server["registry_source"],
                    "risk_tier": server["risk_tier"],
                    "scored_axis_count": len(server["scores"]),
                    "missing_axes": missing_axes,
                    "stale_axes": stale_axes,
                    "last_scored_at": _iso(last_scored_at),
                }
            )

    gaps.sort(key=lambda item: item["server_id"])
    return {
        "generated_at": now.isoformat(),
        "stale_days": int(stale_days),
        "total_servers": len(servers),
        "gap_count": len(gaps),
        "gaps": gaps,
    }


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    Session = sessionmaker(bind=engine)()

    recent = datetime.now(timezone.utc)
    Session.add(McpServerRegistry(server_id="complete", name="Complete"))
    Session.add(McpServerRegistry(server_id="gap", name="Gap"))
    for axis in COVERAGE_AXES:
        Session.add(
            McpLlmAxisScore(
                id=f"complete-{axis}",
                server_id="complete",
                axis_name=axis,
                model_version="test",
                scored_at=recent,
            )
        )
    for axis in COVERAGE_AXES[:2]:
        Session.add(
            McpLlmAxisScore(
                id=f"gap-{axis}",
                server_id="gap",
                axis_name=axis,
                model_version="test",
                scored_at=recent,
            )
        )
    Session.commit()

    result = compute_coverage_gap(Session)
    assert result["gap_count"] == 1
    assert result["gaps"][0]["server_id"] == "gap"
    assert len(result["gaps"][0]["missing_axes"]) == 4
    assert all(gap["server_id"] != "complete" for gap in result["gaps"])
    print("PASS")