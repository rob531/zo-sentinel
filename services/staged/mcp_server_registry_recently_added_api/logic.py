from typing import Any, Dict, List

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


def get_recently_added_servers(
    db: Session = Depends(get_session),
    limit: int = 20,
) -> List[Dict[str, Any]]:
    if isinstance(db, int) and isinstance(limit, Session):
        db, limit = limit, db

    if not isinstance(db, Session):
        raise TypeError("A database session is required")
    if limit < 0:
        raise ValueError("limit must be non-negative")

    if limit == 0:
        return []

    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_seen,
        )
        .where(McpServerRegistry.first_seen.is_not(None))
        .order_by(McpServerRegistry.first_seen.desc())
        .limit(limit)
    )

    rows = db.execute(statement).mappings().all()
    return [dict(row) for row in rows]


get_recently_added = get_recently_added_servers
get_recent_servers = get_recently_added_servers


if __name__ == "__main__":
    from datetime import datetime

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)

    with Session(test_engine) as test_db:
        test_db.add_all(
            [
                McpServerRegistry(
                    server_id="server-oldest",
                    name="Oldest Server",
                    registry_source="registry",
                    trust_score=0.70,
                    verdict="trusted",
                    risk_tier="low",
                    first_seen=datetime(2024, 1, 1, 12, 0, 0),
                    last_seen=datetime(2024, 1, 3, 12, 0, 0),
                ),
                McpServerRegistry(
                    server_id="server-newest",
                    name="Newest Server",
                    registry_source="registry",
                    trust_score=0.95,
                    verdict="approved",
                    risk_tier="low",
                    first_seen=datetime(2024, 1, 3, 12, 0, 0),
                    last_seen=datetime(2024, 1, 3, 13, 0, 0),
                ),
                McpServerRegistry(
                    server_id="server-middle",
                    name="Middle Server",
                    registry_source="registry",
                    trust_score=0.80,
                    verdict="conditional",
                    risk_tier="medium",
                    first_seen=datetime(2024, 1, 2, 12, 0, 0),
                    last_seen=datetime(2024, 1, 3, 11, 0, 0),
                ),
            ]
        )
        test_db.commit()

        that_app = FastAPI()

        @that_app.get("/api/registry/recently-added")
        def recently_added(
            limit: int = Query(10, ge=1, le=100),
            db: Session = Depends(get_session),
        ) -> Dict[str, Any]:
            servers = get_recently_added_servers(db=db, limit=limit)
            return {"count": len(servers), "servers": servers}

        that_app.dependency_overrides[get_session] = lambda: test_db

        with TestClient(that_app) as client:
            response = client.get("/api/registry/recently-added", params={"limit": 3})
            assert response.status_code == 200
            payload = response.json()
            assert payload["count"] == 3
            assert payload["servers"][0]["server_id"] == "server-newest"

    print("PASS")