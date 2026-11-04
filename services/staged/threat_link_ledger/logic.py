"""Threat link ledger data and computation layer."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


_ASSOCIATION_TABLE = "mcp_threat_associations"
_TIMESTAMP_COLUMNS = (
    "observed_at",
    "detected_at",
    "associated_at",
    "last_seen",
    "created_at",
    "updated_at",
    "timestamp",
)


def _association_table(db: Session) -> Table:
    """Reflect the real association table through the active SQLAlchemy bind."""
    return Table(
        _ASSOCIATION_TABLE,
        MetaData(),
        autoload_with=db.get_bind(),
    )


def _timestamp_column(table: Table) -> Optional[Any]:
    for name in _TIMESTAMP_COLUMNS:
        if name in table.c:
            return table.c[name]
    return None


def get_threat_ledger(db: Session, limit: int = 50) -> List[Dict[str, Any]]:
    """Return servers with threat associations, ordered by association count."""
    bounded_limit = max(1, min(int(limit), 500))

    try:
        associations = _association_table(db)
    except NoSuchTableError:
        return []

    if "server_id" not in associations.c:
        return []

    server_id = associations.c["server_id"]
    timestamp_column = _timestamp_column(associations)
    association_count = func.count().label("association_count")
    latest_at = (
        func.max(timestamp_column).label("latest_at")
        if timestamp_column is not None
        else func.null().label("latest_at")
    )

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            association_count,
            latest_at,
        )
        .select_from(McpServerRegistry)
        .join(associations, McpServerRegistry.server_id == server_id)
        .group_by(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .order_by(association_count.desc(), McpServerRegistry.server_id.asc())
        .limit(bounded_limit)
    ).all()

    return [
        {
            "server_id": row.server_id,
            "name": row.name,
            "risk_tier": row.risk_tier,
            "association_count": int(row.association_count),
            "latest_at": row.latest_at,
        }
        for row in rows
    ]


def list_threat_ledger(db: Session, limit: int = 50) -> List[Dict[str, Any]]:
    """Compatibility alias for callers using the list operation name."""
    return get_threat_ledger(db, limit)


def build_threat_ledger(db: Session, limit: int = 50) -> List[Dict[str, Any]]:
    """Compatibility alias for callers using the build operation name."""
    return get_threat_ledger(db, limit)


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine = None
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    McpServerRegistry.__table__.create(engine)

    test_metadata = MetaData()
    threat_associations = Table(
        _ASSOCIATION_TABLE,
        test_metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("server_id", String, nullable=False),
        Column("association_type", String, nullable=False),
        Column("severity", String, nullable=False),
        Column("observed_at", DateTime, nullable=False),
    )
    threat_associations.create(engine)

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-high",
                    name="High Association Server",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="server-low",
                    name="Low Association Server",
                    risk_tier="medium",
                ),
            ]
        )
        session.flush()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.execute(
            insert(threat_associations).values(
                [
                    {
                        "server_id": "server-high",
                        "association_type": "cve",
                        "severity": "critical",
                        "observed_at": now,
                    },
                    {
                        "server_id": "server-high",
                        "association_type": "malware",
                        "severity": "high",
                        "observed_at": now,
                    },
                    {
                        "server_id": "server-low",
                        "association_type": "cve",
                        "severity": "medium",
                        "observed_at": now,
                    },
                ]
            )
        )
        session.commit()

    testing_app = FastAPI()

    @testing_app.get("/api/threat/ledger")
    def threat_ledger_endpoint(
        limit: int = Query(default=50, ge=1, le=500),
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        ledger = get_threat_ledger(db, limit)
        return {"total": len(ledger), "ledger": ledger}

    def override_get_session():
        with SessionLocal() as session:
            yield session

    testing_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(testing_app).get("/api/threat/ledger")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert len(payload["ledger"]) == 2
    assert payload["ledger"][0]["server_id"] == "server-high"
    assert payload["ledger"][0]["association_count"] == 2
    print("PASS")