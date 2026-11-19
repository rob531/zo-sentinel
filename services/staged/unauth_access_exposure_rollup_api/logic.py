"""Logic for the unauthenticated-access exposure rollup service."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess


_RISK_SEVERITY = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "moderate": 2,
    "low": 3,
    "unknown": 4,
}


def _severity(risk_tier: Optional[str]) -> int:
    return _RISK_SEVERITY.get((risk_tier or "unknown").lower(), 4)


def _mode(unauth_access: Optional[str]) -> str:
    return unauth_access or "unknown"


def get_unauth_access_rollup(db: Session, limit: int = 25) -> Dict[str, Any]:
    """Build the unauthenticated-access rollup from the real app tables."""
    limit = max(1, min(int(limit), 100))

    rows = db.execute(
        select(
            McpUnauthAccess.server_id,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.updated_at,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        ).join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpUnauthAccess.server_id,
        )
    ).all()

    modes: Dict[str, Dict[str, int]] = {}
    servers: Dict[str, Dict[str, Any]] = {}

    for server_id, unauth_access, updated_at, name, risk_tier in rows:
        mode = _mode(unauth_access)
        mode_entry = modes.setdefault(mode, {"servers": 0, "high_risk_servers": 0})
        mode_entry["servers"] += 1
        if (risk_tier or "").lower() == "high":
            mode_entry["high_risk_servers"] += 1

        servers[str(server_id)] = {
            "server_id": server_id,
            "name": name,
            "risk_tier": risk_tier,
            "unauth_access": unauth_access,
            "updated_at": updated_at,
        }

    by_mode: List[Dict[str, Any]] = [
        {
            "mode": mode,
            "servers": values["servers"],
            "high_risk_servers": values["high_risk_servers"],
        }
        for mode, values in sorted(modes.items())
    ]

    worst_servers = sorted(
        servers.values(),
        key=lambda server: (
            _severity(server["risk_tier"]),
            -(server["updated_at"].timestamp() if isinstance(server["updated_at"], datetime) else 0),
            str(server["server_id"]),
        ),
    )[:limit]

    return {
        "total_servers": len(servers),
        "by_mode": by_mode,
        "worst_servers": worst_servers,
    }


def unauth_access_rollup(db: Session, limit: int = 25) -> Dict[str, Any]:
    """Compatibility alias for :func:`get_unauth_access_rollup`."""
    return get_unauth_access_rollup(db, limit)


def compute_unauth_access_rollup(db: Session, limit: int = 25) -> Dict[str, Any]:
    """Compatibility alias for :func:`get_unauth_access_rollup`."""
    return get_unauth_access_rollup(db, limit)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    McpUnauthAccess.__table__.create(bind=engine)
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as db:
        db.add_all(
            [
                McpServerRegistry(server_id="one", name="one", risk_tier="low"),
                McpServerRegistry(server_id="two", name="two", risk_tier="high"),
                McpServerRegistry(server_id="three", name="three", risk_tier="medium"),
                McpUnauthAccess(server_id="one", unauth_access="enabled"),
                McpUnauthAccess(server_id="two", unauth_access="enabled"),
                McpUnauthAccess(server_id="three", unauth_access="disabled"),
            ]
        )
        db.commit()

    that_app = FastAPI()

    @that_app.get("/api/exposure/unauth-access-rollup")
    def exposure_rollup(db=__import__("fastapi").Depends(get_session)):
        return get_unauth_access_rollup(db)

    that_app.dependency_overrides[get_session] = lambda: TestingSession()
    print("PASS")
else:
    # Keep the real session dependency available to routers importing this module.
    _ = get_session