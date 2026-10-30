from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink
from services.staged.vuln_advisory_ecosystem_watch.router import router


@contextmanager
def _test_client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    feed TEXT NOT NULL,
                    severity TEXT,
                    ecosystem TEXT,
                    package TEXT,
                    affected_ranges TEXT,
                    source_url TEXT NOT NULL,
                    published_at TEXT,
                    fetched_at TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
                    advisory_id INTEGER NOT NULL,
                    server_id INTEGER NOT NULL,
                    match_basis TEXT NOT NULL,
                    match_confidence REAL NOT NULL,
                    linked_at TEXT NOT NULL
                )
                """
            )
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories
                    (id, feed, severity, ecosystem, package, affected_ranges,
                     source_url, published_at, fetched_at)
                VALUES
                    (1, 'osv', 'critical', 'python', 'requests',
                     '[{"introduced": "0"}]',
                     'https://advisories.example/1', :published_at, :fetched_at),
                    (2, 'osv', 'high', 'python', 'requests',
                     '[{"introduced": "2.0"}]',
                     'https://advisories.example/2', :published_at, :fetched_at),
                    (3, 'osv', 'medium', 'npm', 'lodash',
                     '[{"introduced": "4.0"}]',
                     'https://advisories.example/3', :published_at, :fetched_at)
                """
            ),
            {"published_at": now, "fetched_at": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (id, advisory_id, server_id, match_basis,
                     match_confidence, linked_at)
                VALUES
                    (1, 1, 101, 'package', 0.98, :linked_at),
                    (2, 2, 202, 'package', 0.91, :linked_at)
                """
            ),
            {"linked_at": now},
        )

    def override_get_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(application) as client:
            yield client
    finally:
        engine.dispose()


def _self_test() -> None:
    assert VulnAdvisory.__tablename__ == "vuln_advisories"
    assert VulnLink.__tablename__ == "vuln_links"

    with _test_client() as client:
        response = client.get("/api/vuln/advisories/ecosystem-watch")
        assert response.status_code == 200, response.text

        body = response.json()
        assert body["total_advisories"] == 3

        ecosystems = {item["ecosystem"]: item for item in body["ecosystems"]}
        assert ecosystems["python"]["advisory_count"] == 2
        assert ecosystems["python"]["distinct_servers"] == 2
        assert ecosystems["python"]["severity_mix"]["critical"] == 1
        assert ecosystems["python"]["severity_mix"]["high"] == 1
        assert ecosystems["npm"]["advisory_count"] == 1
        assert ecosystems["npm"]["distinct_servers"] == 0

        top_packages = {
            (item["package"], item["ecosystem"]): item["count"]
            for item in body["top_packages"]
        }
        assert top_packages[("requests", "python")] == 2
        assert top_packages[("lodash", "npm")] == 1


def run_self_test() -> None:
    _self_test()


def main() -> None:
    run_self_test()
    print("PASS")


if __name__ == "__main__":
    main()