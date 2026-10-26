from collections.abc import Generator
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnLink
from services.staged.vuln_link_age_trend_api.router import router


def run_contract_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    VulnLink.__table__.create(bind=engine)

    now = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    rows = [
        {
            "advisory_id": 101,
            "server_id": 201,
            "linked_at": now - timedelta(days=5),
            "match_basis": "cpe",
            "match_value": "cpe:/a:example:one",
            "match_confidence": 0.95,
        },
        {
            "advisory_id": 102,
            "server_id": 201,
            "linked_at": now - timedelta(days=5),
            "match_basis": "package",
            "match_value": "pkg:npm/example-one",
            "match_confidence": 0.90,
        },
        {
            "advisory_id": 102,
            "server_id": 202,
            "linked_at": now - timedelta(days=3),
            "match_basis": "package",
            "match_value": "pkg:npm/example-one",
            "match_confidence": 0.85,
        },
        {
            "advisory_id": 103,
            "server_id": 202,
            "linked_at": now - timedelta(days=3),
            "match_basis": "cpe",
            "match_value": "cpe:/a:example:two",
            "match_confidence": 0.80,
        },
        {
            "advisory_id": 101,
            "server_id": 203,
            "linked_at": now - timedelta(days=1),
            "match_basis": "cpe",
            "match_value": "cpe:/a:example:one",
            "match_confidence": 0.75,
        },
        {
            "advisory_id": 104,
            "server_id": 203,
            "linked_at": now - timedelta(days=1),
            "match_basis": "package",
            "match_value": "pkg:npm/example-three",
            "match_confidence": 0.70,
        },
    ]

    with engine.begin() as connection:
        connection.execute(VulnLink.__table__.insert(), rows)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session() -> Generator[Session, None, None]:
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/vuln/links/age-trend")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["days"] == 30, payload
            assert len(payload["series"]) == 3, payload
            assert payload["totals"]["links"] == 6, payload
            assert payload["totals"]["servers"] == 3, payload
            assert payload["totals"]["advisories"] == 4, payload
            assert payload["stale_days"] == 0, payload
    finally:
        engine.dispose()


if __name__ == "__main__":
    run_contract_test()
    print("PASS")