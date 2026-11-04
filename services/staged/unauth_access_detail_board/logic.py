"""logic.py -- the service's data/computation layer (one concern, one file).

Reads the real application tables through the application's SQLAlchemy session.
The registry is outer-joined so access records for servers missing from the
registry remain visible on the board.
"""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess


def _flag_key(value: Any) -> str:
    """Return the stable JSON key used for an access flag."""
    if value is None:
        return "unknown"
    return str(value)


def get_unauth_access_detail_board(db: Session, limit: int = 100) -> Dict[str, Any]:
    """Build a ranked board of servers with recorded unauthenticated access.

    Counts in ``by_flag`` cover all access records, while ``rows`` is limited
    to the requested board size. Missing registry rows are retained and have
    null registry metadata.
    """
    limit = max(1, min(int(limit), 200))

    flag_rows = db.execute(
        select(McpUnauthAccess.unauth_access, func.count())
        .group_by(McpUnauthAccess.unauth_access)
    ).all()

    by_flag: Dict[str, int] = {}
    total_servers = 0
    for flag, count in flag_rows:
        by_flag[_flag_key(flag)] = int(count)
        total_servers += int(count)

    risk_order = case(
        (McpServerRegistry.risk_tier == "critical", 0),
        (McpServerRegistry.risk_tier == "high", 1),
        (McpServerRegistry.risk_tier == "medium", 2),
        (McpServerRegistry.risk_tier == "low", 3),
        else_=4,
    )

    result = db.execute(
        select(McpUnauthAccess, McpServerRegistry)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpUnauthAccess.server_id,
        )
        .order_by(
            risk_order,
            McpUnauthAccess.updated_at.desc(),
            McpUnauthAccess.server_id,
        )
        .limit(limit)
    ).all()

    rows = [
        {
            "server_id": access.server_id,
            "name": registry.name if registry is not None else None,
            "url": access.url,
            "unauth_access": access.unauth_access,
            "source": access.source,
            "updated_at": access.updated_at,
            "risk_tier": registry.risk_tier if registry is not None else None,
            "verdict": registry.verdict if registry is not None else None,
        }
        for access, registry in result
    ]

    return {
        "total_servers": total_servers,
        "by_flag": by_flag,
        "rows": rows,
    }


def build_unauth_access_detail_board(
    db: Session, limit: int = 100
) -> Dict[str, Any]:
    """Alias for :func:`get_unauth_access_detail_board`."""
    return get_unauth_access_detail_board(db, limit)


def unauth_access_detail_board(
    db: Session, limit: int = 100
) -> Dict[str, Any]:
    """Alias for :func:`get_unauth_access_detail_board`."""
    return get_unauth_access_detail_board(db, limit)


def _self_test() -> None:
    """Exercise the board against a local SQLite-backed application session."""
    from datetime import datetime, timezone

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
    McpUnauthAccess.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with testing_session() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Registry One",
                    url="http://registry-one",
                    risk_tier="high",
                    verdict="confirmed",
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Registry Two",
                    url="http://registry-two",
                    risk_tier="medium",
                    verdict="pending",
                ),
                McpUnauthAccess(
                    server_id="server-1",
                    url="http://one",
                    unauth_access="yes",
                    source="scan",
                ),
                McpUnauthAccess(
                    server_id="server-2",
                    url="http://two",
                    unauth_access="yes",
                    source="scan",
                ),
                McpUnauthAccess(
                    server_id="server-unknown",
                    url="http://unknown",
                    unauth_access="unknown",
                    source="scan",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    local_app = FastAPI()
    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/access/unauth-detail")
    def unauth_detail(
        limit: int = Query(default=100, ge=1, le=200),
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **get_unauth_access_detail_board(db, limit),
        }

    with TestClient(local_app) as client:
        response = client.get("/api/access/unauth-detail")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 3
        assert payload["by_flag"] == {"yes": 2, "unknown": 1}
        assert len(payload["rows"]) == 3
        assert all(row["server_id"] != "registry-only" for row in payload["rows"])
        assert {row["server_id"] for row in payload["rows"]} == {
            "server-1",
            "server-2",
            "server-unknown",
        }

    print("PASS")


if __name__ == "__main__":
    _self_test()