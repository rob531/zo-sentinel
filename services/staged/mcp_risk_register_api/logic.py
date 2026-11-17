from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Iterator, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


LATEST_PER_SERVER = 1
DEFAULT_ENTRY_LIMIT = 100


class RiskRegisterEntry(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    computed_at: Any = None
    overall_risk: Any = None

    class Config:
        extra = "allow"


class RiskRegisterTotals(BaseModel):
    servers: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class RiskRegisterResponse(BaseModel):
    entries: list[RiskRegisterEntry] = []
    totals: RiskRegisterTotals = RiskRegisterTotals()


def _risk_register_sql() -> str:
    return """
        WITH ranked AS (
            SELECT
                risk.*,
                ROW_NUMBER() OVER (
                    PARTITION BY risk.server_id
                    ORDER BY risk.computed_at DESC
                ) AS __zo_latest_rank
            FROM mcp_risk_register AS risk
        )
        SELECT
            ranked.*,
            registry.name AS __zo_server_name,
            registry.risk_tier AS __zo_current_risk_tier
        FROM ranked
        LEFT JOIN mcp_server_registry AS registry
            ON registry.server_id = ranked.server_id
        WHERE ranked.__zo_latest_rank = :latest_per_server
        ORDER BY ranked.computed_at DESC, ranked.server_id
        LIMIT :entry_limit
    """


def _normalise_tier(value: Any) -> Optional[str]:
    if value is None:
        return None
    tier = str(value).strip().lower()
    if tier in {"critical", "high", "medium", "low"}:
        return tier
    return None


def get_risk_register(
    session: Session,
    entry_limit: int = DEFAULT_ENTRY_LIMIT,
) -> Dict[str, Any]:
    entry_limit = max(1, min(int(entry_limit), 1000))

    result = session.execute(
        text(_risk_register_sql()),
        {
            "latest_per_server": LATEST_PER_SERVER,
            "entry_limit": entry_limit,
        },
    )

    entries: list[Dict[str, Any]] = []
    for result_row in result.mappings():
        payload = {
            key: value
            for key, value in dict(result_row).items()
            if key not in {
                "__zo_latest_rank",
                "__zo_server_name",
                "__zo_current_risk_tier",
            }
        }

        server_id = payload.get("server_id")
        if server_id is None:
            continue

        payload["server_id"] = str(server_id)
        payload["name"] = result_row.get("__zo_server_name")
        payload["risk_tier"] = result_row.get("__zo_current_risk_tier")
        payload["computed_at"] = result_row.get("computed_at")
        payload["overall_risk"] = result_row.get("overall_risk")
        entries.append(payload)

    totals = {
        "servers": len(entries),
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
    }

    for entry in entries:
        tier = _normalise_tier(entry.get("risk_tier"))
        if tier is not None:
            totals[tier] += 1

    return {"entries": entries, "totals": totals}


def get_risk_register_data(
    session: Session,
    entry_limit: int = DEFAULT_ENTRY_LIMIT,
) -> Dict[str, Any]:
    return get_risk_register(session, entry_limit=entry_limit)


def get_latest_risk_entries(
    session: Session,
    entry_limit: int = DEFAULT_ENTRY_LIMIT,
) -> Dict[str, Any]:
    return get_risk_register(session, entry_limit=entry_limit)


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["risk-register"])

    @router.get("/risk-register", response_model=RiskRegisterResponse)
    def risk_register(
        limit: int = Query(
            default=DEFAULT_ENTRY_LIMIT,
            ge=1,
            le=1000,
        ),
        session: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_risk_register(session, entry_limit=limit)

    return router


router = create_router()


def _create_fixture_engine() -> Engine:
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _create_fixture_tables(connection: Connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                risk_tier TEXT
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE mcp_risk_register (
                server_id TEXT NOT NULL,
                computed_at TEXT NOT NULL,
                overall_risk TEXT,
                risk_payload TEXT
            )
            """
        )
    )


def _seed_fixture(connection: Connection) -> None:
    connection.execute(
        text(
            """
            INSERT INTO mcp_server_registry
                (server_id, name, risk_tier)
            VALUES
                (:server_id_1, :name_1, :tier_1),
                (:server_id_2, :name_2, :tier_2),
                (:server_id_3, :name_3, :tier_3)
            """
        ),
        {
            "server_id_1": "server-a",
            "name_1": "Server A",
            "tier_1": "high",
            "server_id_2": "server-b",
            "name_2": "Server B",
            "tier_2": "critical",
            "server_id_3": "server-c",
            "name_3": "Server C",
            "tier_3": "low",
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO mcp_risk_register
                (server_id, computed_at, overall_risk, risk_payload)
            VALUES
                (:server_id_1, :computed_at_1, :overall_risk_1, :risk_payload_1),
                (:server_id_2, :computed_at_2, :overall_risk_2, :risk_payload_2),
                (:server_id_3, :computed_at_3, :overall_risk_3, :risk_payload_3),
                (:server_id_1, :computed_at_4, :overall_risk_4, :risk_payload_4)
            """
        ),
        {
            "server_id_1": "server-a",
            "computed_at_1": "2024-01-01T00:00:00Z",
            "overall_risk_1": "stale-risk",
            "risk_payload_1": '{"version": 1}',
            "server_id_2": "server-b",
            "computed_at_2": "2024-01-02T00:00:00Z",
            "overall_risk_2": "known-risk",
            "risk_payload_2": '{"version": 2}',
            "server_id_3": "server-c",
            "computed_at_3": "2024-01-03T00:00:00Z",
            "overall_risk_3": "low-risk",
            "risk_payload_3": '{"version": 3}',
            "computed_at_4": "2024-01-04T00:00:00Z",
            "overall_risk_4": "known-risk",
            "risk_payload_4": '{"version": 4}',
        },
    )


def _self_test() -> None:
    from fastapi.testclient import TestClient

    assert McpServerRegistry.__tablename__ == "mcp_server_registry"

    engine = _create_fixture_engine()
    fixture_session = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        _create_fixture_tables(connection)
        _seed_fixture(connection)

    def override_get_session() -> Iterator[Session]:
        with fixture_session() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(create_router())

    with TestClient(test_app) as client:
        response = client.get("/api/risk-register")

    assert response.status_code == 200
    body = response.json()
    assert len(body["entries"]) == 3
    assert body["totals"]["servers"] == 3
    assert any(entry["overall_risk"] == "known-risk" for entry in body["entries"])
    assert all(
        sum(
            1
            for entry in body["entries"]
            if entry["server_id"] == server_id
        )
        == 1
        for server_id in {entry["server_id"] for entry in body["entries"]}
    )

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()