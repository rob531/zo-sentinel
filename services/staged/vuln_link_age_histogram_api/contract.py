from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnLink
from services.staged.vuln_link_age_histogram_api.router import router


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    VulnLink.__table__.create(bind=engine)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    linked_offsets = (timedelta(0), timedelta(days=1), timedelta(days=8), timedelta(days=15))

    with engine.begin() as connection:
        for index, offset in enumerate(linked_offsets, start=1):
            connection.execute(
                text(
                    """
                    INSERT INTO vuln_links
                        (id, advisory_id, server_id, match_basis, match_value,
                         match_confidence, linked_at)
                    VALUES
                        (:id, :advisory_id, :server_id, :match_basis, :match_value,
                         :match_confidence, :linked_at)
                    """
                ),
                {
                    "id": index,
                    "advisory_id": index,
                    "server_id": index,
                    "match_basis": "cve",
                    "match_value": f"CVE-2024-{index:04d}",
                    "match_confidence": 0.9,
                    "linked_at": now - offset,
                },
            )

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get(
                "/api/vuln/links/age-histogram",
                params={"bucket_days": 7},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 4, payload
        assert isinstance(payload["buckets"], list), payload

        bucket_starts = [bucket["bucket_start"] for bucket in payload["buckets"]]
        assert bucket_starts == sorted(bucket_starts), payload
        assert len(set(bucket_starts)) >= 3, payload
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run_self_test()