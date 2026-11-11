from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink
from .logic import get_age_buckets

router = APIRouter(prefix="/api")


class AgeBucket(BaseModel):
    bucket: str
    count: int
    avg_match_confidence: Optional[float] = None


class MatchBasisCount(BaseModel):
    match_basis: str
    count: int


class AgeBucketsResponse(BaseModel):
    total_links: int
    buckets: list[AgeBucket]
    top_match_basis: list[MatchBasisCount]


@router.get("/vuln-links/age-buckets", response_model=AgeBucketsResponse)
def vuln_link_age_buckets(
    session: Session = Depends(get_session),
) -> dict:
    return get_age_buckets(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
                    advisory_id INTEGER,
                    server_id INTEGER,
                    match_basis TEXT,
                    match_value TEXT,
                    match_confidence REAL,
                    linked_at TIMESTAMP NULL
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (id, advisory_id, server_id, match_basis, match_value,
                     match_confidence, linked_at)
                VALUES
                    (1, 1, 1, 'cve', 'CVE-2026-0001', 0.9, :now),
                    (2, 1, 2, 'package', 'unsafe-package', 0.7, :ten_days),
                    (3, 2, 3, 'cve', 'CVE-2025-0002', 0.5, :sixty_days),
                    (4, 2, 4, 'package', 'old-package', 0.3, :four_hundred_days),
                    (5, 3, 5, 'cve', 'CVE-2024-0003', NULL, NULL)
                """
            ),
            {
                "now": now,
                "ten_days": now - timedelta(days=10),
                "sixty_days": now - timedelta(days=60),
                "four_hundred_days": now - timedelta(days=400),
            },
        )

    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: TestSession()

    with TestSession() as session:
        response = get_age_buckets(session)

    buckets = {item["bucket"]: item for item in response["buckets"]}
    assert response["total_links"] == 5
    assert buckets["never"]["count"] == 1
    assert sum(item["count"] for item in response["buckets"]) == response["total_links"]
    for item in response["buckets"]:
        if item["count"] and item["avg_match_confidence"] is not None:
            assert 0.0 <= item["avg_match_confidence"] <= 1.0

    from fastapi.testclient import TestClient

    with TestClient(test_app) as client:
        http_response = client.get("/api/vuln-links/age-buckets")
        assert http_response.status_code == 200
        payload = http_response.json()
        assert payload["total_links"] == 5
        payload_buckets = {item["bucket"]: item for item in payload["buckets"]}
        assert payload_buckets["never"]["count"] == 1
        assert sum(item["count"] for item in payload["buckets"]) == payload["total_links"]

    print("PASS")