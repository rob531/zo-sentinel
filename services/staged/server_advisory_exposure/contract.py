from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


_REGISTRY_TABLE = McpServerRegistry.__tablename__
_LINK_TABLE = VulnLink.__tablename__
_ADVISORY_TABLE = VulnAdvisory.__tablename__

router = APIRouter(prefix="/api")

_SEVERITY_RANK = {
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


class ServerAdvisory(BaseModel):
    advisory_id: int
    feed: str
    summary: Optional[str] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    match_basis: str
    match_value: str
    match_confidence: float
    source_url: str
    published_at: Optional[datetime] = None


class ServerAdvisoriesResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    advisory_count: int
    max_severity: Optional[str] = None
    advisories: list[ServerAdvisory]


def _severity_rank(value: Optional[str]) -> int:
    if value is None:
        return 0
    return _SEVERITY_RANK.get(value.strip().upper(), 0)


@router.get(
    "/servers/{server_id}/advisories",
    response_model=ServerAdvisoriesResponse,
)
def get_server_advisories(
    server_id: str,
    session: Session = Depends(get_session),
) -> ServerAdvisoriesResponse:
    statement = text(
        f"""
        SELECT
            s.server_id AS server_id,
            s.name AS server_name,
            s.risk_tier AS risk_tier,
            a.id AS advisory_id,
            a.feed AS feed,
            a.summary AS summary,
            a.severity AS severity,
            a.ecosystem AS ecosystem,
            a.package AS package,
            l.match_basis AS match_basis,
            l.match_value AS match_value,
            l.match_confidence AS match_confidence,
            a.source_url AS source_url,
            a.published_at AS published_at
        FROM {_REGISTRY_TABLE} AS s
        LEFT JOIN {_LINK_TABLE} AS l
            ON l.server_id = s.server_id
        LEFT JOIN {_ADVISORY_TABLE} AS a
            ON a.id = l.advisory_id
        WHERE s.server_id = :server_id
        ORDER BY
            CASE WHEN a.published_at IS NULL THEN 1 ELSE 0 END ASC,
            a.published_at DESC,
            a.id DESC
        LIMIT 50
        """
    )
    rows = session.execute(statement, {"server_id": server_id}).mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found")

    advisories: list[ServerAdvisory] = []
    seen_advisory_ids: set[int] = set()
    for row in rows:
        if row["advisory_id"] is None:
            continue
        advisory_id = int(row["advisory_id"])
        if advisory_id in seen_advisory_ids:
            continue
        seen_advisory_ids.add(advisory_id)
        advisories.append(
            ServerAdvisory(
                advisory_id=advisory_id,
                feed=row["feed"],
                summary=row["summary"],
                severity=row["severity"],
                ecosystem=row["ecosystem"],
                package=row["package"],
                match_basis=row["match_basis"],
                match_value=row["match_value"],
                match_confidence=row["match_confidence"],
                source_url=row["source_url"],
                published_at=row["published_at"],
            )
        )

    max_severity: Optional[str] = None
    max_rank = 0
    for advisory in advisories:
        rank = _severity_rank(advisory.severity)
        if rank > max_rank:
            max_rank = rank
            max_severity = advisory.severity.strip().upper()
        elif rank == max_rank and rank == 0 and max_severity is None:
            max_severity = (advisory.severity or "UNKNOWN").strip().upper()

    if advisories and max_severity is None:
        max_severity = "UNKNOWN"

    return ServerAdvisoriesResponse(
        server_id=str(row["server_id"]),
        name=row["server_name"],
        risk_tier=row["risk_tier"],
        advisory_count=len(advisories),
        max_severity=max_severity,
        advisories=advisories,
    )


def run_self_test() -> None:
    engine = create_test_engine()
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as session:
        session.execute(
            text(
                f"""
                CREATE TABLE {_REGISTRY_TABLE} (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    risk_tier TEXT,
                    trust_score REAL
                )
                """
            )
        )
        session.execute(
            text(
                f"""
                CREATE TABLE {_ADVISORY_TABLE} (
                    id INTEGER PRIMARY KEY,
                    feed TEXT NOT NULL,
                    summary TEXT,
                    severity TEXT,
                    ecosystem TEXT,
                    package TEXT,
                    source_url TEXT NOT NULL,
                    published_at TEXT
                )
                """
            )
        )
        session.execute(
            text(
                f"""
                CREATE TABLE {_LINK_TABLE} (
                    id INTEGER PRIMARY KEY,
                    advisory_id INTEGER NOT NULL,
                    server_id TEXT NOT NULL,
                    match_basis TEXT NOT NULL,
                    match_value TEXT NOT NULL,
                    match_confidence REAL NOT NULL
                )
                """
            )
        )

        session.execute(
            text(
                f"""
                INSERT INTO {_REGISTRY_TABLE}
                    (server_id, name, risk_tier, trust_score)
                VALUES
                    (:server_id, :name, :risk_tier, :trust_score)
                """
            ),
            {
                "server_id": "server-a",
                "name": "Example MCP Server",
                "risk_tier": "MEDIUM",
                "trust_score": 0.75,
            },
        )
        session.execute(
            text(
                f"""
                INSERT INTO {_ADVISORY_TABLE}
                    (id, feed, summary, severity, ecosystem, package,
                     source_url, published_at)
                VALUES
                    (101, 'OSV', 'High severity advisory', 'HIGH',
                     'PyPI', 'example-package', 'https://example.test/101',
                     '2025-01-02T00:00:00'),
                    (102, 'OSV', 'Low severity advisory', 'LOW',
                     'npm', 'other-package', 'https://example.test/102',
                     '2025-01-01T00:00:00'),
                    (201, 'OSV', 'Other server only advisory', 'CRITICAL',
                     'PyPI', 'private-package', 'https://example.test/201',
                     '2025-01-03T00:00:00')
                """
            )
        )
        session.execute(
            text(
                f"""
                INSERT INTO {_LINK_TABLE}
                    (id, advisory_id, server_id, match_basis,
                     match_value, match_confidence)
                VALUES
                    (1, 101, 'server-a', 'package', 'example-package', 0.99),
                    (2, 102, 'server-a', 'package', 'other-package', 0.95)
                """
            )
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/servers/server-a/advisories")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["server_id"] == "server-a"
            assert payload["advisory_count"] == 2
            assert payload["max_severity"] == "HIGH"
            assert {item["advisory_id"] for item in payload["advisories"]} == {101, 102}
            assert all(
                item["advisory_id"] != 201
                and item["summary"] != "Other server only advisory"
                for item in payload["advisories"]
            )
    finally:
        engine.dispose()

    print("PASS")


def create_test_engine():
    from sqlalchemy import create_engine

    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


run_contract = run_self_test
run = run_self_test
main = run_self_test


if __name__ == "__main__":
    run_self_test()