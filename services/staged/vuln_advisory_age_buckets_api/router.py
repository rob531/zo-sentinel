from __future__ import annotations

import inspect
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory
from . import logic as _logic

try:
    from .logic import get_age_buckets as _get_age_buckets
except ImportError:
    _get_age_buckets = None


class AgeBucket(BaseModel):
    bucket: str
    count: int
    by_severity: Dict[str, int]


class FeedCount(BaseModel):
    feed: str
    count: int


class AgeBucketsResponse(BaseModel):
    total: int
    buckets: List[AgeBucket]
    feeds: List[FeedCount]


router = APIRouter(prefix="/api")


def _find_logic_callable() -> Callable[[Session], Any]:
    if _get_age_buckets is not None:
        return _get_age_buckets

    candidates = (
        "get_age_buckets",
        "get_age_bucket_summary",
        "get_age_bucket_report",
        "get_age_bucket_data",
        "get_vuln_advisory_age_buckets",
        "get_vuln_advisories_age_buckets",
        "get_vulnerability_age_buckets",
        "get_age_distribution",
        "get_age_bucket_distribution",
        "build_age_buckets",
        "compute_age_buckets",
        "fetch_age_buckets",
        "get_buckets",
    )
    for name in candidates:
        candidate = getattr(_logic, name, None)
        if callable(candidate):
            return candidate

    public_callables = [
        value
        for name, value in vars(_logic).items()
        if not name.startswith("_")
        and callable(value)
        and not isinstance(value, type)
    ]
    named_matches = [
        value
        for name, value in vars(_logic).items()
        if not name.startswith("_")
        and callable(value)
        and not isinstance(value, type)
        and "age" in name.lower()
        and "bucket" in name.lower()
    ]
    if named_matches:
        return named_matches[0]
    if len(public_callables) == 1:
        return public_callables[0]

    raise RuntimeError("No age-bucket data function is available in the service logic module")


_LOGIC_CALLABLE = _find_logic_callable()


def _as_payload(value: Any) -> Any:
    if isinstance(value, BaseModel):
        if hasattr(value, "model_dump"):
            return value.model_dump()
        return value.dict()
    if is_dataclass(value):
        return asdict(value)
    return value


@router.get(
    "/vuln/advisories/age-buckets",
    response_model=AgeBucketsResponse,
    summary="Vulnerability advisory age-bucket distribution",
)
async def get_vuln_advisory_age_buckets(
    session: Session = Depends(get_session),
) -> Any:
    payload = _LOGIC_CALLABLE(session)
    if inspect.isawaitable(payload):
        payload = await payload
    return _as_payload(payload)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    that_app = FastAPI()
    that_app.include_router(router)

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id VARCHAR PRIMARY KEY,
                    feed VARCHAR,
                    summary VARCHAR,
                    severity VARCHAR,
                    ecosystem VARCHAR,
                    package VARCHAR,
                    affected_ranges TEXT,
                    aliases TEXT,
                    source_url VARCHAR,
                    published_at DATETIME,
                    fetched_at DATETIME,
                    identities TEXT,
                    content_hash VARCHAR
                )
                """
            )
        )

        current_time = datetime.now(timezone.utc).replace(tzinfo=None)
        test_rows = [
            {
                "id": "1",
                "feed": "alpha",
                "summary": "recent high advisory",
                "severity": "high",
                "ecosystem": "pypi",
                "package": "example-one",
                "affected_ranges": "[]",
                "aliases": "[]",
                "source_url": "https://example.invalid/1",
                "published_at": current_time - timedelta(days=3),
                "fetched_at": current_time,
                "identities": "[]",
                "content_hash": "hash-1",
            },
            {
                "id": "2",
                "feed": "alpha",
                "summary": "recent critical advisory",
                "severity": "critical",
                "ecosystem": "npm",
                "package": "example-two",
                "affected_ranges": "[]",
                "aliases": "[]",
                "source_url": "https://example.invalid/2",
                "published_at": current_time - timedelta(days=15),
                "fetched_at": current_time,
                "identities": "[]",
                "content_hash": "hash-2",
            },
            {
                "id": "3",
                "feed": "beta",
                "summary": "older medium advisory",
                "severity": "medium",
                "ecosystem": "pypi",
                "package": "example-three",
                "affected_ranges": "[]",
                "aliases": "[]",
                "source_url": "https://example.invalid/3",
                "published_at": current_time - timedelta(days=45),
                "fetched_at": current_time,
                "identities": "[]",
                "content_hash": "hash-3",
            },
            {
                "id": "4",
                "feed": "beta",
                "summary": "old low advisory",
                "severity": "low",
                "ecosystem": "npm",
                "package": "example-four",
                "affected_ranges": "[]",
                "aliases": "[]",
                "source_url": "https://example.invalid/4",
                "published_at": current_time - timedelta(days=120),
                "fetched_at": current_time,
                "identities": "[]",
                "content_hash": "hash-4",
            },
            {
                "id": "5",
                "feed": "gamma",
                "summary": "undated advisory",
                "severity": "unknown",
                "ecosystem": "pypi",
                "package": "example-five",
                "affected_ranges": "[]",
                "aliases": "[]",
                "source_url": "https://example.invalid/5",
                "published_at": None,
                "fetched_at": current_time,
                "identities": "[]",
                "content_hash": "hash-5",
            },
        ]
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories (
                    id, feed, summary, severity, ecosystem, package,
                    affected_ranges, aliases, source_url, published_at,
                    fetched_at, identities, content_hash
                ) VALUES (
                    :id, :feed, :summary, :severity, :ecosystem, :package,
                    :affected_ranges, :aliases, :source_url, :published_at,
                    :fetched_at, :identities, :content_hash
                )
                """
            ),
            test_rows,
        )

    def override_get_session():
        test_session = TestingSessionLocal()
        try:
            yield test_session
        finally:
            test_session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/advisories/age-buckets")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 5, body

        buckets = {bucket["bucket"]: bucket for bucket in body["buckets"]}
        assert buckets["unknown"]["count"] == 1, body
        for bucket in body["buckets"]:
            assert sum(bucket["by_severity"].values()) == bucket["count"], body

    print("PASS")