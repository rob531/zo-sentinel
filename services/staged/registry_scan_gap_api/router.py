from __future__ import annotations

import inspect
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import read_scan_gaps

router = APIRouter(prefix="/api")


def _read_gaps(db: Session, days: int, limit: int) -> dict:
    parameters = inspect.signature(read_scan_gaps).parameters
    kwargs: dict = {"days": days, "limit": limit}

    session_parameter = next(
        (
            name
            for name, parameter in parameters.items()
            if name not in {"days", "limit"}
            and parameter.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
        ),
        None,
    )
    if session_parameter is not None:
        kwargs[session_parameter] = db

    return read_scan_gaps(**kwargs)


@router.get("/registry/scan-gaps")
def registry_scan_gaps(
    days: int = 90,
    limit: int = 200,
    db: Session = Depends(get_session),
) -> dict:
    return _read_gaps(db, days, limit)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.utcnow()
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="never-scanned",
                    name="Never scanned",
                    registry_source="test",
                    risk_tier="high",
                    trust_score=0.1,
                    scan_count=0,
                    first_seen=now - timedelta(days=30),
                    last_seen=now,
                    last_scanned=None,
                    last_assessed=None,
                ),
                McpServerRegistry(
                    server_id="stale-server",
                    name="Stale server",
                    registry_source="test",
                    risk_tier="medium",
                    trust_score=0.5,
                    scan_count=1,
                    first_seen=now - timedelta(days=30),
                    last_seen=now,
                    last_scanned=now - timedelta(days=10),
                    last_assessed=now - timedelta(days=10),
                ),
                McpServerRegistry(
                    server_id="current-server",
                    name="Current server",
                    registry_source="test",
                    risk_tier="low",
                    trust_score=0.9,
                    scan_count=2,
                    first_seen=now - timedelta(days=30),
                    last_seen=now,
                    last_scanned=now,
                    last_assessed=now,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get("/api/registry/scan-gaps?days=1&limit=200")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["count"] == 2, payload

        by_id = {item["server_id"]: item for item in payload["gaps"]}
        assert by_id["never-scanned"]["state"] == "never_scanned", payload
        assert isinstance(by_id["stale-server"]["age_days"], int), payload
        assert by_id["stale-server"]["state"] == "stale", payload

    print("PASS")