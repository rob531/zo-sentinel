"""logic.py -- unauthenticated-access exposure rollup computation."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess


_TIER_SEVERITY = {
    "critical": 6,
    "high": 5,
    "medium": 4,
    "moderate": 3,
    "low": 2,
    "minimal": 1,
    "info": 0,
    "unknown": -1,
}


def _severity(risk_tier: str | None) -> int:
    return _TIER_SEVERITY.get((risk_tier or "unknown").strip().lower(), -1)


def _updated_at_key(value: Any) -> float:
    if value is None:
        return float("-inf")
    if isinstance(value, (datetime, date)):
        try:
            return float(value.timestamp())
        except (AttributeError, OverflowError, OSError):
            return float("-inf")
    return float("-inf")


def _is_high_risk(risk_tier: str | None) -> bool:
    return (risk_tier or "").strip().lower() in {"critical", "high"}


def get_unauth_access_rollup(db: Session, limit: int = 25) -> Dict[str, Any]:
    """Return the unauthenticated-access exposure rollup.

    The access table is joined to the registry so that each access record is
    associated with the server's current name, tier, and assessment verdict.
    """
    try:
        requested_limit = int(limit)
    except (TypeError, ValueError):
        requested_limit = 25
    requested_limit = max(1, min(requested_limit, 100))

    rows = db.execute(
        select(
            McpUnauthAccess.server_id,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.updated_at,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpUnauthAccess.server_id,
        )
    ).all()

    mode_counts: Dict[str, int] = {}
    mode_high_risk_counts: Dict[str, int] = {}
    for row in rows:
        mode = row.unauth_access or "unknown"
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
        if _is_high_risk(row.risk_tier):
            mode_high_risk_counts[mode] = mode_high_risk_counts.get(mode, 0) + 1

    by_mode = [
        {
            "mode": mode,
            "servers": mode_counts[mode],
            "high_risk_servers": mode_high_risk_counts.get(mode, 0),
        }
        for mode in sorted(mode_counts)
    ]

    sorted_rows = list(rows)
    sorted_rows.sort(key=lambda row: str(row.server_id or ""))
    sorted_rows.sort(
        key=lambda row: _updated_at_key(row.updated_at),
        reverse=True,
    )
    sorted_rows.sort(
        key=lambda row: _severity(row.risk_tier),
        reverse=True,
    )

    worst_servers: List[Dict[str, Any]] = [
        {
            "server_id": row.server_id,
            "name": row.name,
            "risk_tier": row.risk_tier,
            "unauth_access": row.unauth_access or "unknown",
            "updated_at": row.updated_at,
        }
        for row in sorted_rows[:requested_limit]
    ]

    return {
        "total_servers": len(rows),
        "by_mode": by_mode,
        "worst_servers": worst_servers,
    }


def get_exposure_rollup(db: Session, limit: int = 25) -> Dict[str, Any]:
    """Compatibility alias for the exposure rollup computation."""
    return get_unauth_access_rollup(db, limit)


def get_rollup(db: Session, limit: int = 25) -> Dict[str, Any]:
    """Compatibility alias for the exposure rollup computation."""
    return get_unauth_access_rollup(db, limit)


if __name__ == "__main__":
    from datetime import datetime, timezone

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
    McpServerRegistry.__table__.create(engine)
    McpUnauthAccess.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="srv-alpha",
                    name="Alpha",
                    risk_tier="low",
                    verdict="ok",
                    url="https://alpha.example",
                ),
                McpServerRegistry(
                    server_id="srv-beta",
                    name="Beta",
                    risk_tier="high",
                    verdict="exposed",
                    url="https://beta.example",
                ),
                McpServerRegistry(
                    server_id="srv-gamma",
                    name="Gamma",
                    risk_tier="medium",
                    verdict="review",
                    url="https://gamma.example",
                ),
            ]
        )
        session.add_all(
            [
                McpUnauthAccess(
                    server_id="srv-alpha",
                    url="https://alpha.example",
                    unauth_access="open",
                    source="probe",
                    updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                ),
                McpUnauthAccess(
                    server_id="srv-beta",
                    url="https://beta.example",
                    unauth_access="open",
                    source="probe",
                    updated_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
                ),
                McpUnauthAccess(
                    server_id="srv-gamma",
                    url="https://gamma.example",
                    unauth_access="none",
                    source="probe",
                    updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/exposure/unauth-access-rollup")
    def exposure_rollup(session: Session = Depends(get_session)):
        return get_unauth_access_rollup(session)

    with TestClient(that_app) as client:
        response = client.get("/api/exposure/unauth-access-rollup")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 3
        modes = {entry["mode"]: entry for entry in payload["by_mode"]}
        assert set(modes) == {"open", "none"}
        assert modes["open"]["servers"] == 2
        assert modes["none"]["servers"] == 1
        assert any(
            server["server_id"] == "srv-beta"
            and server["risk_tier"] == "high"
            for server in payload["worst_servers"]
        )

    print("PASS")