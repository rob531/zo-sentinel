"""HTTP surface for score ingest-lag reporting."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_score_ingest_lag
except ImportError:
    try:
        from .logic import get_ingest_lag as get_score_ingest_lag
    except ImportError:
        from .logic import score_ingest_lag as get_score_ingest_lag


router = APIRouter(prefix="/api/scores", tags=["scores"])


class ServerIngestLag(BaseModel):
    server_id: str
    risk_tier: str | None = None
    last_scored_at: datetime | None = None
    last_axis_score_at: datetime | None = None
    lag_hours: float
    axes_present: list[str] = []


class IngestLagSummary(BaseModel):
    scored: int
    stale_over_24h: int
    stale_over_72h: int
    median_lag_hours: float | None = None


class IngestLagResponse(BaseModel):
    days: int
    servers: list[ServerIngestLag]
    summary: IngestLagSummary


@router.get("/ingest-lag", response_model=IngestLagResponse)
def get_ingest_lag(
    days: int = Query(30, ge=1),
    db: Session = Depends(get_session),
) -> IngestLagResponse:
    result = get_score_ingest_lag(db, days=days)

    if isinstance(result, Mapping):
        payload: dict[str, Any] = dict(result)
    elif isinstance(result, tuple) and len(result) == 2:
        payload = {"servers": result[0], "summary": result[1]}
    else:
        payload = {"servers": result}

    payload.setdefault("days", days)
    return IngestLagResponse(**payload)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpLlmAxisScore, McpServerRegistry

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    now = datetime.now(timezone.utc)
    servers = [
        ("server-old", "high", now - timedelta(days=3)),
        ("server-recent-1", "medium", now - timedelta(hours=1)),
        ("server-recent-2", "low", now - timedelta(hours=2)),
    ]
    for server_id, risk_tier, _ in servers:
        db.add(
            McpServerRegistry(
                server_id=server_id,
                name=server_id,
                risk_tier=risk_tier,
                last_seen=now,
            )
        )

    for index, (server_id, _, scored_at) in enumerate(servers, start=1):
        db.add(
            McpLlmAxisScore(
                id=index,
                server_id=server_id,
                axis_name="security",
                model_version="test-model",
                scored_at=scored_at,
            )
        )

    db.commit()
    test_app.dependency_overrides[get_session] = lambda: db
    try:
        response = TestClient(test_app).get(
            "/api/scores/ingest-lag",
            params={"days": 30},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["summary"]["scored"] == 3, body
        assert body["summary"]["stale_over_24h"] >= 1, body
        assert any(
            server["server_id"] == "server-old"
            and server["lag_hours"] >= 72
            for server in body["servers"]
        ), body
    finally:
        db.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()