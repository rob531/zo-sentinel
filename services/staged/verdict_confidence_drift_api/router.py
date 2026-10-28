from __future__ import annotations

from datetime import datetime, timedelta, timezone
from inspect import isawaitable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_verdict_confidence_drift

router = APIRouter()


@router.get("/api/registry/verdict-confidence-drift")
async def verdict_confidence_drift(
    days: int = 30,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    result = get_verdict_confidence_drift(session, days)
    if isawaitable(result):
        result = await result
    return result


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    McpServerRegistry.__table__.create(engine)

    now = datetime.now(timezone.utc)
    seeds = (
        ("vcd-safe", "vcd-safe", "safe", 0.20, "low", now),
        ("vcd-risky", "vcd-risky", "risky", 0.50, "medium", now - timedelta(days=1)),
        ("vcd-unknown", "vcd-unknown", "unknown", 0.90, "high", now - timedelta(days=2)),
    )

    with testing_session() as session:
        for server_id, name, verdict, confidence, risk_tier, assessed_at in seeds:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    verdict=verdict,
                    confidence=confidence,
                    risk_tier=risk_tier,
                    last_assessed=assessed_at,
                )
            )
        session.commit()

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = lambda: session

        with TestClient(test_app) as client:
            response = client.get(
                "/api/registry/verdict-confidence-drift",
                params={"days": 3},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["days"] == 3
            assert isinstance(payload["bands"], list)
            assert payload["bands"]
            assert any(row["count"] == 1 for row in payload["bands"])
            assert "totals" in payload

    engine.dispose()


if __name__ == "__main__":
    _run_self_test()
    print("PASS")