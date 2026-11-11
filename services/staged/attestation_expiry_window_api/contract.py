from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

WindowName = Literal[
    "expired",
    "expiring_7d",
    "expiring_30d",
    "expiring_90d",
    "valid",
]

WINDOW_ORDER: tuple[WindowName, ...] = (
    "expired",
    "expiring_7d",
    "expiring_30d",
    "expiring_90d",
    "valid",
)


class ExpiryWindowCount(BaseModel):
    window: WindowName
    count: int


class ExpiryWindowServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    verdict: Optional[str] = None
    expires_at: Optional[datetime] = None


class ExpiryWindowResponse(BaseModel):
    days: int
    windows: list[ExpiryWindowCount]
    servers: list[ExpiryWindowServer]


router = APIRouter(prefix="/api")


def _expiry_column(session: Session) -> str:
    columns = {
        column["name"]
        for column in inspect(session.get_bind()).get_columns("mcp_attestations")
    }
    if "expires_at" in columns:
        return "expires_at"
    if "issued_at" in columns:
        return "issued_at"
    raise RuntimeError("mcp_attestations must contain expires_at or issued_at")


def get_expiry_window(days: int, session: Session) -> ExpiryWindowResponse:
    now = datetime.now(timezone.utc)
    expiry_column = _expiry_column(session)
    expiry_expression = f"a.{expiry_column}"

    window_sql = f"""
        SELECT
            CASE
                WHEN {expiry_expression} <= :now THEN 'expired'
                WHEN {expiry_expression} <= :now_7d THEN 'expiring_7d'
                WHEN {expiry_expression} <= :now_30d THEN 'expiring_30d'
                WHEN {expiry_expression} <= :now_90d THEN 'expiring_90d'
                ELSE 'valid'
            END AS window,
            COUNT(*) AS count
        FROM mcp_attestations a
        GROUP BY
            CASE
                WHEN {expiry_expression} <= :now THEN 'expired'
                WHEN {expiry_expression} <= :now_7d THEN 'expiring_7d'
                WHEN {expiry_expression} <= :now_30d THEN 'expiring_30d'
                WHEN {expiry_expression} <= :now_90d THEN 'expiring_90d'
                ELSE 'valid'
            END
    """
    parameters = {
        "now": now,
        "now_7d": now + timedelta(days=7),
        "now_30d": now + timedelta(days=30),
        "now_90d": now + timedelta(days=90),
    }
    count_rows = session.execute(text(window_sql), parameters).mappings().all()
    counts = {str(row["window"]): int(row["count"]) for row in count_rows}

    server_sql = f"""
        SELECT
            a.server_id AS server_id,
            r.name AS name,
            a.verdict AS verdict,
            {expiry_expression} AS expires_at
        FROM mcp_attestations a
        LEFT JOIN {McpServerRegistry.__tablename__} r
            ON r.server_id = a.server_id
        ORDER BY a.server_id, {expiry_expression}
    """
    server_rows = session.execute(text(server_sql), parameters).mappings().all()

    return ExpiryWindowResponse(
        days=days,
        windows=[
            ExpiryWindowCount(window=window, count=counts.get(window, 0))
            for window in WINDOW_ORDER
        ],
        servers=[
            ExpiryWindowServer(
                server_id=str(row["server_id"]),
                name=row["name"],
                verdict=row["verdict"],
                expires_at=row["expires_at"],
            )
            for row in server_rows
        ],
    )


@router.get(
    "/attestations/expiry-window",
    response_model=ExpiryWindowResponse,
)
def expiry_window(
    days: int = Query(default=30, ge=0, le=3650),
    session: Session = Depends(get_session),
) -> ExpiryWindowResponse:
    return get_expiry_window(days, session)


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_attestations (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    verdict TEXT,
                    issued_at TIMESTAMP,
                    expires_at TIMESTAMP
                )
                """
            )
        )

    now = datetime.now(timezone.utc)
    with session_factory.begin() as session:
        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name)
                VALUES (:server_id, :name)
                """
            ),
            [
                {"server_id": "server-expired", "name": "Expired Server"},
                {"server_id": "server-soon", "name": "Soon Expiring Server"},
            ],
        )
        session.execute(
            text(
                """
                INSERT INTO mcp_attestations
                    (server_id, verdict, issued_at, expires_at)
                VALUES
                    (:server_id, :verdict, :issued_at, :expires_at)
                """
            ),
            [
                {
                    "server_id": "server-expired",
                    "verdict": "verified",
                    "issued_at": now - timedelta(days=30),
                    "expires_at": now - timedelta(days=1),
                },
                {
                    "server_id": "server-soon",
                    "verdict": "verified",
                    "issued_at": now - timedelta(days=10),
                    "expires_at": now + timedelta(days=3),
                },
                {
                    "server_id": "server-thirty",
                    "verdict": "conditional",
                    "issued_at": now - timedelta(days=10),
                    "expires_at": now + timedelta(days=20),
                },
                {
                    "server_id": "server-ninety",
                    "verdict": "verified",
                    "issued_at": now - timedelta(days=10),
                    "expires_at": now + timedelta(days=60),
                },
                {
                    "server_id": "server-valid",
                    "verdict": "verified",
                    "issued_at": now - timedelta(days=10),
                    "expires_at": now + timedelta(days=180),
                },
            ],
        )

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/attestations/expiry-window", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["days"] == 30
        assert sum(item["count"] for item in payload["windows"]) == 5
        counts = {item["window"]: item["count"] for item in payload["windows"]}
        assert counts["expired"] == 1
        assert len(payload["servers"]) == 5

    print("PASS")


if __name__ == "__main__":
    run()