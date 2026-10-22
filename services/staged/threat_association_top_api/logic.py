from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_TOP_ASSOCIATIONS_SQL = text(
    """
    SELECT
        registry.server_id AS server_id,
        registry.name AS name,
        registry.registry_source AS registry_source,
        registry.risk_tier AS risk_tier,
        COUNT(*) AS association_count,
        COUNT(DISTINCT association.threat_type) AS distinct_threat_types,
        MAX(association.observed_at) AS latest_at
    FROM mcp_threat_associations AS association
    INNER JOIN mcp_server_registry AS registry
        ON registry.server_id = association.server_id
    WHERE association.observed_at >= :cutoff
    GROUP BY
        registry.server_id,
        registry.name,
        registry.registry_source,
        registry.risk_tier
    ORDER BY
        association_count DESC,
        latest_at DESC,
        registry.server_id ASC
    LIMIT :row_limit
    """
)

_TOTAL_ASSOCIATIONS_SQL = text(
    """
    SELECT COUNT(*)
    FROM mcp_threat_associations AS association
    INNER JOIN mcp_server_registry AS registry
        ON registry.server_id = association.server_id
    WHERE association.observed_at >= :cutoff
    """
)


def get_top_threat_associations(
    db: Session = Depends(get_session),
    days: int = 30,
    limit: int = 50,
) -> Dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")
    if limit < 1:
        raise ValueError("limit must be at least 1")

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    params = {"cutoff": cutoff, "row_limit": limit}

    total = int(db.execute(_TOTAL_ASSOCIATIONS_SQL, params).scalar_one())
    rows = db.execute(_TOP_ASSOCIATIONS_SQL, params).mappings().all()

    servers = [
        {
            "server_id": row["server_id"],
            "name": row["name"],
            "registry_source": row["registry_source"],
            "risk_tier": row["risk_tier"],
            "association_count": int(row["association_count"]),
            "distinct_threat_types": int(row["distinct_threat_types"]),
            "latest_at": row["latest_at"],
        }
        for row in rows
    ]

    return {
        "generated_at": datetime.now(timezone.utc),
        "window_days": days,
        "total": total,
        "servers": servers,
    }


get_top_associations = get_top_threat_associations
get_threat_association_top = get_top_threat_associations
get_threat_associations_top = get_top_threat_associations
get_top_threat_association = get_top_threat_associations


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_threat_associations (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    threat_type TEXT NOT NULL,
                    observed_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, risk_tier)
                VALUES
                    (:server_id, :name, :registry_source, :risk_tier)
                """
            ),
            [
                {
                    "server_id": "server-a",
                    "name": "Server A",
                    "registry_source": "internal",
                    "risk_tier": "high",
                },
                {
                    "server_id": "server-b",
                    "name": "Server B",
                    "registry_source": "internal",
                    "risk_tier": "medium",
                },
            ],
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO mcp_threat_associations
                    (server_id, threat_type, observed_at)
                VALUES
                    (:server_id, :threat_type, :observed_at)
                """
            ),
            [
                {
                    "server_id": "server-a",
                    "threat_type": "malware",
                    "observed_at": now - timedelta(hours=3),
                },
                {
                    "server_id": "server-a",
                    "threat_type": "cve",
                    "observed_at": now - timedelta(hours=2),
                },
                {
                    "server_id": "server-a",
                    "threat_type": "exfiltration",
                    "observed_at": now - timedelta(hours=1),
                },
                {
                    "server_id": "server-b",
                    "threat_type": "malware",
                    "observed_at": now - timedelta(hours=4),
                },
            ],
        )

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threat/associations/top")
    def top_associations(
        days: int = 30,
        limit: int = 50,
        db: Session = Depends(get_session),
    ):
        return get_top_threat_associations(db=db, days=days, limit=limit)

    with TestClient(that_app) as client:
        response = client.get("/api/threat/associations/top", params={"days": 30, "limit": 50})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert payload["window_days"] == 30
        assert payload["servers"][0]["server_id"] == "server-a"
        assert payload["servers"][0]["association_count"] == 3

        limited = client.get(
            "/api/threat/associations/top",
            params={"days": 30, "limit": 1},
        )
        assert limited.status_code == 200
        assert len(limited.json()["servers"]) == 1

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()