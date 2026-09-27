from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import SummaryResponse, get_registry_summary

router = APIRouter(prefix="/api", tags=["registry_summary_stats"])


@router.get("/registry/summary", response_model=SummaryResponse)
def registry_summary(session: Session = Depends(get_session)) -> SummaryResponse:
    return get_registry_summary(session)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seeds = [
        ("srv-1", "Alpha", "high", 1, 2),
        ("srv-2", "Beta", "medium", 2, 3),
        ("srv-3", "Gamma", "low", 5, 10),
        ("srv-4", "Delta", "high", 15, 20),
        ("srv-5", "Epsilon", "medium", 35, 40),
    ]
    with TestSessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    risk_tier=risk_tier,
                    last_scanned=now - timedelta(days=scanned_days),
                    last_assessed=now - timedelta(days=assessed_days),
                )
                for server_id, name, risk_tier, scanned_days, assessed_days in seeds
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/summary")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    assert payload["total"] == 5, f"Expected total 5, got {payload['total']}"
    assert payload["tiers"] == {"high": 2, "medium": 2, "low": 1}, payload["tiers"]
    for window in ("window_24h", "window_7d", "window_30d"):
        value = payload["freshness"][window]
        assert isinstance(value, int) and value >= 0, f"Invalid {window}: {value}"
    print("PASS")