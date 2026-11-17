from datetime import datetime, timedelta, timezone
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from services.staged.vuln_advisory_ageing_board.router import router as service_router


_metadata = MetaData()

vuln_advisories = Table(
    "vuln_advisories",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("feed", String(255), nullable=False),
    Column("severity", String(64), nullable=False),
    Column("ecosystem", String(128), nullable=False),
    Column("package", String(255), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("source_url", Text),
    Column("content_hash", String(128)),
)

vuln_links = Table(
    "vuln_links",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("server_id", Integer, nullable=False),
    Column("advisory_id", Integer, nullable=False),
)

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
_metadata.create_all(engine)


def get_test_session() -> Session:
    """Return a session bound to the isolated in-memory acceptance database."""
    return SessionLocal()


def override_get_session() -> Iterator[Session]:
    """FastAPI dependency override for the application's real session dependency."""
    session = get_test_session()
    try:
        yield session
    finally:
        session.close()


app = FastAPI()
router_paths = {getattr(route, "path", "") for route in service_router.routes}
if any(path.startswith("/api/") for path in router_paths):
    app.include_router(service_router)
else:
    app.include_router(service_router, prefix="/api")
app.dependency_overrides[get_session] = override_get_session


def seed_data() -> None:
    session = get_test_session()
    try:
        session.execute(vuln_advisories.delete())
        session.execute(vuln_links.delete())
        session.flush()

        now = datetime.now(timezone.utc)
        advisory_rows = [
            {
                "id": 1,
                "feed": "acceptance-feed",
                "severity": "HIGH",
                "ecosystem": "python",
                "package": "example-zero-to-seven",
                "published_at": now - timedelta(days=1),
                "fetched_at": now,
                "source_url": "https://example.invalid/advisories/1",
                "content_hash": "acceptance-hash-1",
            },
            {
                "id": 2,
                "feed": "acceptance-feed",
                "severity": "HIGH",
                "ecosystem": "python",
                "package": "example-eight-to-thirty",
                "published_at": now - timedelta(days=10),
                "fetched_at": now,
                "source_url": "https://example.invalid/advisories/2",
                "content_hash": "acceptance-hash-2",
            },
            {
                "id": 3,
                "feed": "acceptance-feed",
                "severity": "HIGH",
                "ecosystem": "python",
                "package": "example-thirty-one-to-ninety",
                "published_at": now - timedelta(days=40),
                "fetched_at": now,
                "source_url": "https://example.invalid/advisories/3",
                "content_hash": "acceptance-hash-3",
            },
            {
                "id": 4,
                "feed": "acceptance-feed",
                "severity": "HIGH",
                "ecosystem": "python",
                "package": "example-over-ninety",
                "published_at": now - timedelta(days=120),
                "fetched_at": now,
                "source_url": "https://example.invalid/advisories/4",
                "content_hash": "acceptance-hash-4",
            },
            {
                "id": 5,
                "feed": "acceptance-feed",
                "severity": "HIGH",
                "ecosystem": "python",
                "package": "example-zero-to-seven-second",
                "published_at": now - timedelta(days=2),
                "fetched_at": now,
                "source_url": "https://example.invalid/advisories/5",
                "content_hash": "acceptance-hash-5",
            },
        ]
        session.execute(vuln_advisories.insert(), advisory_rows)
        session.execute(
            vuln_links.insert(),
            [
                {"id": 1, "server_id": 101, "advisory_id": 1},
                {"id": 2, "server_id": 102, "advisory_id": 2},
            ],
        )
        session.commit()
    finally:
        session.close()


def run_self_test() -> None:
    seed_data()

    with TestClient(app) as client:
        response = client.get(
            "/api/vuln/advisory-ageing",
            params={"ecosystem": "python", "days": 365},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    buckets = payload["buckets"]

    assert len(buckets) >= 3, payload
    assert payload["unlinked_total"] == 3, payload
    assert payload["total"] == 5, payload
    assert payload["days"] == 365, payload

    bucket_order = ["0-7d", "8-30d", "31-90d", ">90d"]
    observed_order = [bucket["bucket"] for bucket in buckets]
    assert observed_order == [
        bucket for bucket in bucket_order if bucket in observed_order
    ], observed_order
    assert observed_order[-1] == ">90d", observed_order

    print("PASS")


if __name__ == "__main__":
    run_self_test()