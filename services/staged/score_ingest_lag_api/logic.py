"""logic.py -- score ingest-lag data and computation layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    value = _utc(value)
    return value.isoformat() if value is not None else None


def get_ingest_lag(db: Session, days: int = 7) -> Dict[str, Any]:
    """Return per-server score ingest lag and aggregate lag statistics.

    ``days`` limits the score-observation window.  The registry timestamp is
    the most recent registry observation available for the server; the
    authoritative registry model exposes ``last_seen`` rather than a
    ``last_scored`` column.
    """
    if days < 1:
        raise ValueError("days must be at least 1")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    rows = db.execute(
        select(McpServerRegistry, McpLlmAxisScore)
        .join(
            McpLlmAxisScore,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .order_by(McpServerRegistry.server_id, McpLlmAxisScore.scored_at)
    ).all()

    by_server: Dict[str, Dict[str, Any]] = {}
    for registry, score in rows:
        scored_at = _utc(score.scored_at)
        if scored_at is None or scored_at < cutoff:
            continue

        server_id = registry.server_id
        entry = by_server.setdefault(
            server_id,
            {
                "registry": registry,
                "latest": None,
                "axes": set(),
            },
        )
        entry["axes"].add(score.axis_name)
        if entry["latest"] is None or scored_at > entry["latest"][0]:
            entry["latest"] = (scored_at, score)

    servers: List[Dict[str, Any]] = []
    lag_values: List[float] = []

    for server_id in sorted(by_server):
        entry = by_server[server_id]
        registry = entry["registry"]
        latest_at, _ = entry["latest"]

        registry_time = _utc(registry.last_seen)
        if registry_time is None:
            registry_time = _utc(registry.last_assessed)
        if registry_time is None:
            registry_time = _utc(registry.last_scanned)

        if registry_time is None:
            lag_hours: Optional[float] = None
        else:
            lag_hours = max(0.0, (registry_time - latest_at).total_seconds() / 3600.0)
            lag_values.append(lag_hours)

        servers.append(
            {
                "server_id": server_id,
                "risk_tier": registry.risk_tier or "unknown",
                "last_scored_at": _iso(registry_time),
                "last_axis_score_at": _iso(latest_at),
                "lag_hours": round(lag_hours, 2) if lag_hours is not None else None,
                "axes_present": sorted(entry["axes"]),
            }
        )

    stale_over_24h = sum(
        1 for value in lag_values if value > 24.0
    )
    stale_over_72h = sum(
        1 for value in lag_values if value > 72.0
    )

    return {
        "days": days,
        "servers": servers,
        "summary": {
            "scored": len(servers),
            "stale_over_24h": stale_over_24h,
            "stale_over_72h": stale_over_72h,
            "median_lag_hours": round(float(median(lag_values)), 2)
            if lag_values
            else 0.0,
        },
    }


def compute_ingest_lag(db: Session, days: int = 7) -> Dict[str, Any]:
    """Compatibility alias for callers using the computation-style name."""
    return get_ingest_lag(db, days)


def get_score_ingest_lag(db: Session, days: int = 7) -> Dict[str, Any]:
    """Compatibility alias for callers using the service-style name."""
    return get_ingest_lag(db, days)


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
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    registry_rows = [
        McpServerRegistry(
            server_id="server-1",
            risk_tier="high",
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="server-2",
            risk_tier="medium",
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="server-3",
            risk_tier="low",
            last_seen=now,
        ),
    ]
    score_rows = [
        McpLlmAxisScore(
            id="score-1",
            server_id="server-1",
            axis_name="security",
            model_version="test-model",
            scored_at=now - timedelta(days=3, hours=1),
        ),
        McpLlmAxisScore(
            id="score-2",
            server_id="server-2",
            axis_name="quality",
            model_version="test-model",
            scored_at=now - timedelta(days=2, hours=1),
        ),
        McpLlmAxisScore(
            id="score-3",
            server_id="server-3",
            axis_name="reliability",
            model_version="test-model",
            scored_at=now - timedelta(days=1, hours=1),
        ),
    ]

    with TestingSession() as session:
        session.add_all(registry_rows)
        session.add_all(score_rows)
        session.commit()

        def override_get_session():
            with TestingSession() as session:
                yield session

        that_app = FastAPI()
        that_app.dependency_overrides[get_session] = override_get_session

        @that_app.get("/api/scores/ingest-lag")
        def ingest_lag_endpoint(
            days: int = Query(7, ge=1),
            db: Session = Depends(get_session),
        ):
            return get_ingest_lag(db, days)

        response = TestClient(that_app).get(
            "/api/scores/ingest-lag", params={"days": 7}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["scored"] == 3
        assert payload["summary"]["stale_over_24h"] >= 1
        assert any(
            server["server_id"] == "server-1"
            and server["lag_hours"] > 24
            for server in payload["servers"]
        )
        print("PASS")