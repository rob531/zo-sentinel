from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

from .logic import get_unlinked_advisory_report

router = APIRouter(prefix="/api")


class SeverityCount(BaseModel):
    severity: Optional[str] = None
    count: int


class EcosystemCount(BaseModel):
    ecosystem: Optional[str] = None
    count: int


class FeedCount(BaseModel):
    feed: Optional[str] = None
    count: int


class UnlinkedAdvisory(BaseModel):
    id: int
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    published_at: Optional[datetime] = None
    link_count: int = 0


class UnlinkedAdvisoryReportResponse(BaseModel):
    total_unlinked: int
    by_severity: list[SeverityCount] = Field(default_factory=list)
    by_ecosystem: list[EcosystemCount] = Field(default_factory=list)
    by_feed: list[FeedCount] = Field(default_factory=list)
    oldest_unlinked_published_at: Optional[str] = None
    advisories: list[UnlinkedAdvisory] = Field(default_factory=list)


@router.get(
    "/vuln/advisories/unlinked",
    response_model=UnlinkedAdvisoryReportResponse,
)
def list_unlinked_advisories(
    session: Session = Depends(get_session),
) -> UnlinkedAdvisoryReportResponse:
    return get_unlinked_advisory_report(session)


def _run_self_test() -> None:
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = Session(bind=engine)

    advisory_table = VulnAdvisory.__tablename__
    link_table = VulnLink.__tablename__

    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE {advisory_table} (
                    id INTEGER PRIMARY KEY,
                    feed TEXT,
                    severity TEXT,
                    ecosystem TEXT,
                    package TEXT,
                    published_at TEXT,
                    fetched_at TEXT,
                    affected_ranges TEXT,
                    aliases TEXT,
                    content_hash TEXT,
                    identities TEXT,
                    source_url TEXT,
                    summary TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                f"""
                CREATE TABLE {link_table} (
                    id INTEGER PRIMARY KEY,
                    advisory_id INTEGER,
                    server_id INTEGER,
                    match_basis TEXT,
                    match_confidence REAL,
                    linked_at TEXT,
                    match_value TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                f"""
                INSERT INTO {advisory_table}
                    (id, feed, severity, ecosystem, package, published_at, fetched_at)
                VALUES
                    (1, 'osv', 'HIGH', 'PyPI', 'linked-package',
                     '2024-03-01T00:00:00', '2024-03-02T00:00:00'),
                    (2, 'osv', 'MEDIUM', 'npm', 'linked-package',
                     '2024-02-01T00:00:00', '2024-02-02T00:00:00'),
                    (3, 'osv', 'HIGH', 'PyPI', 'unlinked-package',
                     '2024-01-01T00:00:00', '2024-01-02T00:00:00'),
                    (4, 'osv', 'LOW', 'npm', 'unlinked-package',
                     '2023-12-01T00:00:00', '2023-12-02T00:00:00')
                """
            )
        )
        connection.execute(
            text(
                f"""
                INSERT INTO {link_table}
                    (id, advisory_id, server_id, match_basis, match_confidence)
                VALUES
                    (1, 1, 101, 'package', 0.95),
                    (2, 2, 102, 'package', 0.90)
                """
            )
        )

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: session_factory

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/vuln/advisories/unlinked")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_unlinked"] == 2, payload
        assert sum(
            bucket["count"]
            for bucket in payload["by_severity"]
            if bucket["severity"] == "HIGH"
        ) == 1, payload
        advisory_ids = {advisory["id"] for advisory in payload["advisories"]}
        assert 1 not in advisory_ids, payload
        assert 2 not in advisory_ids, payload
        assert {3, 4}.issubset(advisory_ids), payload
    finally:
        session_factory.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()