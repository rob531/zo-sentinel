from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_threat_association_rank

router = APIRouter(prefix="/api", tags=["threat"])


class ThreatAssociationRankParams(BaseModel):
    limit: int = 50
    days: int = 30


class ThreatAssociationServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    indicator_count: int
    last_seen: str | None = None


class ThreatAssociationRankResponse(BaseModel):
    days: int
    count: int
    servers: list[ThreatAssociationServer] = Field(default_factory=list)


def _serialize_last_seen(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _server_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


@router.get(
    "/threat/associations/rank",
    response_model=ThreatAssociationRankResponse,
    summary="Rank servers by distinct threat indicators",
)
def threat_association_rank(
    params: ThreatAssociationRankParams = Depends(),
    session: Session = Depends(get_session),
) -> ThreatAssociationRankResponse:
    limit = max(1, min(200, params.limit))
    days = max(1, min(365, params.days))

    result = get_threat_association_rank(
        session=session,
        limit=limit,
        days=days,
    )
    rows = result.get("servers", []) if isinstance(result, Mapping) else result

    servers = [
        ThreatAssociationServer(
            server_id=str(_server_value(row, "server_id", "")),
            name=str(_server_value(row, "name", "")),
            risk_tier=str(_server_value(row, "risk_tier", "")),
            indicator_count=int(_server_value(row, "indicator_count", 0)),
            last_seen=_serialize_last_seen(_server_value(row, "last_seen")),
        )
        for row in rows
    ]

    return ThreatAssociationRankResponse(
        days=days,
        count=len(servers),
        servers=servers,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    confidence REAL,
                    description TEXT,
                    first_seen TEXT,
                    last_assessed TEXT,
                    last_scanned TEXT,
                    last_seen TEXT,
                    meta TEXT,
                    name TEXT,
                    registry_source TEXT,
                    risk_tier TEXT,
                    scan_count INTEGER,
                    server_id TEXT PRIMARY KEY,
                    trust_score REAL,
                    url TEXT,
                    verdict TEXT,
                    verdict_reasoning TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_threat_associations (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    indicator TEXT,
                    indicator_id TEXT,
                    indicator_type TEXT,
                    indicator_value TEXT,
                    associated_at TEXT,
                    association_timestamp TEXT,
                    observed_at TEXT,
                    created_at TEXT,
                    first_seen TEXT,
                    last_seen TEXT,
                    timestamp TEXT,
                    confidence REAL,
                    source TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, risk_tier)
                VALUES
                    ('server-high', 'High Risk Server', 'HIGH_RISK_ISOLATED'),
                    ('server-medium', 'Medium Risk Server', 'MEDIUM_RISK'),
                    ('server-empty', 'Unassociated Server', 'LOW_RISK')
                """
            )
        )

        now = datetime.utcnow().isoformat()
        rows = []
        for index in range(4):
            indicator = f"indicator-high-{index}"
            rows.append(
                {
                    "server_id": "server-high",
                    "indicator": indicator,
                    "indicator_id": indicator,
                    "indicator_type": indicator,
                    "indicator_value": indicator,
                    "associated_at": now,
                    "association_timestamp": now,
                    "observed_at": now,
                    "created_at": now,
                    "first_seen": now,
                    "last_seen": now,
                    "timestamp": now,
                    "confidence": 0.9,
                    "source": "self-test",
                }
            )
        for index in range(2):
            indicator = f"indicator-medium-{index}"
            rows.append(
                {
                    "server_id": "server-medium",
                    "indicator": indicator,
                    "indicator_id": indicator,
                    "indicator_type": indicator,
                    "indicator_value": indicator,
                    "associated_at": now,
                    "association_timestamp": now,
                    "observed_at": now,
                    "created_at": now,
                    "first_seen": now,
                    "last_seen": now,
                    "timestamp": now,
                    "confidence": 0.8,
                    "source": "self-test",
                }
            )
        connection.execute(
            text(
                """
                INSERT INTO mcp_threat_associations (
                    server_id, indicator, indicator_id, indicator_type,
                    indicator_value, associated_at, association_timestamp,
                    observed_at, created_at, first_seen, last_seen, timestamp,
                    confidence, source
                ) VALUES (
                    :server_id, :indicator, :indicator_id, :indicator_type,
                    :indicator_value, :associated_at, :association_timestamp,
                    :observed_at, :created_at, :first_seen, :last_seen, :timestamp,
                    :confidence, :source
                )
                """
            ),
            rows,
        )

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/threat/associations/rank",
        params={"limit": 50, "days": 30},
    )
    response.raise_for_status()
    payload = response.json()
    assert payload["count"] == 2
    assert payload["servers"][0]["indicator_count"] == 4
    assert payload["servers"][0]["risk_tier"] == "HIGH_RISK_ISOLATED"
    assert all(server["server_id"] != "server-empty" for server in payload["servers"])
    print("PASS")