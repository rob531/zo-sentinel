from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_verdict_aging
except ImportError:
    try:
        from .logic import get_verdict_aging_data as get_verdict_aging
    except ImportError:
        from .logic import get_verdict_aging_stats as get_verdict_aging


class VerdictAgingBand(BaseModel):
    band: str
    verdict: str | None = None
    risk_tier: str | None = None
    count: int = Field(ge=0)
    avg_confidence: float | None = None


class VerdictAgingTotals(BaseModel):
    servers: int = Field(ge=0)
    overdue: int = Field(ge=0)


class VerdictAgingResponse(BaseModel):
    days: int
    bands: list[VerdictAgingBand]
    totals: VerdictAgingTotals


router = APIRouter(prefix="/api", tags=["server-verdict-aging"])


async def _run_logic(session: Session, days: int) -> Any:
    signature = inspect.signature(get_verdict_aging)
    parameters = signature.parameters

    kwargs: dict[str, Any] = {}
    for name in parameters:
        if name in {"session", "db", "db_session"}:
            kwargs[name] = session
        elif name == "days":
            kwargs[name] = days

    if kwargs:
        result = get_verdict_aging(**kwargs)
    else:
        try:
            result = get_verdict_aging(session, days)
        except TypeError:
            result = get_verdict_aging(days, session)

    if inspect.isawaitable(result):
        result = await result
    return result


@router.get(
    "/servers/verdict-aging",
    response_model=VerdictAgingResponse,
    summary="Get server verdict aging bands",
)
async def verdict_aging(
    days: int = Query(30, ge=0, le=3650),
    session: Session = Depends(get_session),
) -> Any:
    result = await _run_logic(session, days)
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    elif hasattr(result, "dict"):
        result = result.dict()
    return result


def _self_test() -> None:
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
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    verdict TEXT,
                    risk_tier TEXT,
                    confidence REAL,
                    last_assessed DATETIME,
                    last_scanned DATETIME,
                    last_seen DATETIME,
                    first_seen DATETIME,
                    description TEXT,
                    meta TEXT,
                    registry_source TEXT,
                    scan_count INTEGER,
                    trust_score REAL,
                    url TEXT,
                    verdict_reasoning TEXT
                )
                """
            )
        )

        now = datetime.now(timezone.utc)
        rows = [
            ("srv-fresh", "fresh", "trusted", "low", 0.95, now),
            (
                "srv-aging",
                "aging",
                "review",
                "medium",
                0.80,
                now - timedelta(days=3),
            ),
            (
                "srv-stale",
                "stale",
                "untrusted",
                "high",
                0.60,
                now - timedelta(days=10),
            ),
            (
                "srv-overdue",
                "overdue",
                "untrusted",
                "critical",
                0.40,
                now - timedelta(days=40),
            ),
        ]
        for server_id, name, verdict, risk_tier, confidence, assessed in rows:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry (
                        server_id, name, verdict, risk_tier, confidence,
                        last_assessed, last_seen, scan_count
                    ) VALUES (
                        :server_id, :name, :verdict, :risk_tier, :confidence,
                        :last_assessed, :last_seen, 1
                    )
                    """
                ),
                {
                    "server_id": server_id,
                    "name": name,
                    "verdict": verdict,
                    "risk_tier": risk_tier,
                    "confidence": confidence,
                    "last_assessed": assessed,
                    "last_seen": now,
                },
            )

    session = SessionLocal()
    application = FastAPI()
    application.include_router(router)

    def override_get_session():
        yield session

    application.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(application) as client:
            response = client.get("/api/servers/verdict-aging", params={"days": 30})
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["days"] == 30
            assert payload["totals"]["servers"] == 4
            counts = {entry["band"]: entry["count"] for entry in payload["bands"]}
            assert counts.get("fresh") == 1
            assert counts.get("aging") == 1
            assert counts.get("stale") == 1
            assert counts.get("overdue") == 1
            assert payload["totals"]["overdue"] == 1
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()