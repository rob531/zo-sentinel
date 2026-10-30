from __future__ import annotations

from inspect import isawaitable

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_scan_count_bands

router = APIRouter()


@router.get("/api/registry/scan-count-bands")
async def registry_scan_count_bands(session: Session = Depends(get_session)):
    result = get_scan_count_bands(session)
    if isawaitable(result):
        result = await result
    return result


def _run_self_test() -> None:
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import insert
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)

    with Session(engine) as session:
        now = datetime.now(timezone.utc)
        rows = [
            {
                "server_id": "registry-scan-0",
                "name": "registry-scan-0",
                "registry_source": "contract",
                "trust_score": 0.8,
                "verdict": "trusted",
                "risk_tier": "low",
                "scan_count": 0,
                "first_seen": now,
                "last_seen": now,
                "last_scanned": now,
                "last_assessed": now,
            },
            {
                "server_id": "registry-scan-1",
                "name": "registry-scan-1",
                "registry_source": "contract",
                "trust_score": 0.7,
                "verdict": "trusted",
                "risk_tier": None,
                "scan_count": 0,
                "first_seen": now,
                "last_seen": now,
                "last_scanned": None,
                "last_assessed": now,
            },
            {
                "server_id": "registry-scan-2",
                "name": "registry-scan-2",
                "registry_source": "contract",
                "trust_score": 0.9,
                "verdict": "trusted",
                "risk_tier": "low",
                "scan_count": 1,
                "first_seen": now,
                "last_seen": now,
                "last_scanned": now,
                "last_assessed": now,
            },
            {
                "server_id": "registry-scan-3",
                "name": "registry-scan-3",
                "registry_source": "contract",
                "trust_score": 0.9,
                "verdict": "trusted",
                "risk_tier": "medium",
                "scan_count": 4,
                "first_seen": now,
                "last_seen": now,
                "last_scanned": now,
                "last_assessed": now,
            },
            {
                "server_id": "registry-scan-4",
                "name": "registry-scan-4",
                "registry_source": "contract",
                "trust_score": 0.9,
                "verdict": "trusted",
                "risk_tier": "high",
                "scan_count": 12,
                "first_seen": now,
                "last_seen": now,
                "last_scanned": now,
                "last_assessed": now,
            },
        ]
        session.execute(insert(McpServerRegistry), rows)
        session.commit()

    def override_get_session():
        with Session(engine) as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/scan-count-bands")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 5
        assert payload["never_scanned"] == 1
        assert payload["banded_no_scan"] == 1
        assert next(
            band["count"] for band in payload["bands"] if band["band"] == "0"
        ) == 2
        assert any(
            source["registry_source"] == "contract" and source["never_scanned"] == 1
            for source in payload["by_source"]
        )

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()