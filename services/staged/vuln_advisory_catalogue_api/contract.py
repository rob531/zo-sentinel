from __future__ import annotations

import importlib
import json
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _load_real_data_layer():
    """Load the application's real database dependency.

    The service supports the common database module layouts used by the project while
    keeping the HTTP contract in this single file.
    """
    module_names = (
        "services.staged.vuln_advisory_catalogue_api.data",
        "services.staged.vuln_advisory_catalogue_api.database",
        "services.staged.vuln_advisory_catalogue_api.dependencies",
        "app.database",
        "app.db",
        "app.dependencies",
        "database",
        "db",
        "dependencies",
        "services.database",
        "services.db",
        "services.data.database",
        "services.data.db",
        "services.common.database",
        "common.database",
        "backend.app.database",
        "backend.app.db",
    )

    loaded_modules = []
    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        loaded_modules.append(module)
        for dependency_name in ("get_db", "get_session", "get_db_session", "db_session"):
            dependency = getattr(module, dependency_name, None)
            if callable(dependency):
                return dependency

    for module in loaded_modules:
        for factory_name in ("SessionLocal", "session_factory", "SessionMaker"):
            factory = getattr(module, factory_name, None)
            if callable(factory):
                def dependency_from_factory():
                    session = factory()
                    try:
                        yield session
                    finally:
                        session.close()

                return dependency_from_factory

        engine = getattr(module, "engine", None)
        if engine is not None:
            factory = sessionmaker(bind=engine)

            def dependency_from_engine():
                session = factory()
                try:
                    yield session
                finally:
                    session.close()

            return dependency_from_engine

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        engine = create_engine(database_url)
        factory = sessionmaker(bind=engine)

        def dependency_from_url():
            session = factory()
            try:
                yield session
            finally:
                session.close()

        return dependency_from_url

    raise RuntimeError("The real database dependency could not be imported")


get_session = _load_real_data_layer()

app = FastAPI(title="Vulnerability Advisory Catalogue API")


def _decode_json_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (dict, str, bytes, bytearray)):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if isinstance(decoded, list):
            return decoded
        if decoded is None:
            return []
        return [decoded]
    return [value]


def _iso_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _serialise_item(row: Any) -> Dict[str, Any]:
    affected_ranges = _decode_json_list(row.affected_ranges)
    return {
        "id": str(row.id),
        "feed": str(row.feed),
        "summary": str(row.summary),
        "severity": str(row.severity),
        "ecosystem": row.ecosystem,
        "package": row.package,
        "published_at": _iso_or_none(row.published_at),
        "source_url": row.source_url,
        "aliases": [str(alias) for alias in _decode_json_list(row.aliases)],
        "affected_ranges": [
            value for value in affected_ranges if isinstance(value, dict)
        ],
    }


@app.get("/api/vuln-advisories")
def get_vuln_advisories(
    severity: Optional[str] = Query(default=None),
    ecosystem: Optional[str] = Query(default=None),
    package: Optional[str] = Query(default=None),
    published_since: Optional[date] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    conditions = []
    parameters: Dict[str, Any] = {
        "query_limit": limit,
        "query_offset": offset,
    }

    severity_values = [
        value.strip().lower()
        for value in (severity or "").split(",")
        if value.strip()
    ]
    if severity_values:
        conditions.append("LOWER(severity) IN :severity_values")
        parameters["severity_values"] = severity_values

    if ecosystem is not None:
        conditions.append("ecosystem = :ecosystem")
        parameters["ecosystem"] = ecosystem

    if package is not None:
        conditions.append(
            "LOWER(COALESCE(\"package\", '')) LIKE :package_pattern ESCAPE '\\'"
        )
        parameters["package_pattern"] = f"%{_escape_like(package.lower())}%"

    if published_since is not None:
        conditions.append("published_at >= :published_since")
        parameters["published_since"] = published_since.isoformat()

    if q is not None:
        conditions.append(
            "(LOWER(COALESCE(summary, '')) LIKE :search_pattern ESCAPE '\\' "
            "OR LOWER(COALESCE(\"package\", '')) LIKE :search_pattern ESCAPE '\\')"
        )
        parameters["search_pattern"] = f"%{_escape_like(q.lower())}%"

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    sql = f"""
        WITH filtered AS (
            SELECT
                id,
                feed,
                summary,
                severity,
                ecosystem,
                "package",
                affected_ranges,
                aliases,
                source_url,
                published_at
            FROM vuln_advisories
            {where_clause}
        ),
        counted AS (
            SELECT
                filtered.*,
                COUNT(*) OVER () AS total_count
            FROM filtered
        ),
        paged AS (
            SELECT *
            FROM counted
            ORDER BY published_at DESC NULLS LAST, id ASC
            LIMIT :query_limit OFFSET :query_offset
        ),
        totals AS (
            SELECT COALESCE(MAX(total_count), 0) AS total_count
            FROM counted
        )
        SELECT
            paged.id,
            paged.feed,
            paged.summary,
            paged.severity,
            paged.ecosystem,
            paged."package",
            paged.affected_ranges,
            paged.aliases,
            paged.source_url,
            paged.published_at,
            totals.total_count
        FROM totals
        LEFT JOIN paged ON 1 = 1
        ORDER BY paged.published_at DESC NULLS LAST, paged.id ASC
    """

    statement = text(sql)
    if severity_values:
        statement = statement.bindparams(
            bindparam("severity_values", expanding=True)
        )

    rows = session.execute(statement, parameters).mappings().all()
    items = [
        _serialise_item(row)
        for row in rows
        if row["id"] is not None
    ]
    total = int(rows[0]["total_count"]) if rows else 0

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


def _run_acceptance_test() -> None:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_factory = sessionmaker(
        bind=test_engine,
        autocommit=False,
        autoflush=False,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id VARCHAR PRIMARY KEY,
                    feed VARCHAR NOT NULL,
                    summary TEXT NOT NULL,
                    severity VARCHAR NOT NULL,
                    ecosystem VARCHAR,
                    "package" VARCHAR,
                    affected_ranges TEXT,
                    aliases TEXT,
                    source_url VARCHAR,
                    published_at VARCHAR,
                    fetched_at VARCHAR,
                    identities TEXT,
                    content_hash VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories (
                    id,
                    feed,
                    summary,
                    severity,
                    ecosystem,
                    "package",
                    affected_ranges,
                    aliases,
                    source_url,
                    published_at,
                    fetched_at,
                    identities,
                    content_hash
                ) VALUES (
                    :id,
                    :feed,
                    :summary,
                    :severity,
                    :ecosystem,
                    :package,
                    :affected_ranges,
                    :aliases,
                    :source_url,
                    :published_at,
                    :fetched_at,
                    :identities,
                    :content_hash
                )
                """
            ),
            [
                {
                    "id": "vuln-001",
                    "feed": "example-security-feed",
                    "summary": "Critical request parsing vulnerability",
                    "severity": "CRITICAL",
                    "ecosystem": "PyPI",
                    "package": "requests",
                    "affected_ranges": json.dumps(
                        [{"introduced": "0", "fixed": "2.32.0"}]
                    ),
                    "aliases": json.dumps(["CVE-2024-0001"]),
                    "source_url": "https://security.example.test/CVE-2024-0001",
                    "published_at": "2024-02-01T12:00:00+00:00",
                    "fetched_at": "2024-02-02T12:00:00+00:00",
                    "identities": json.dumps(["sha256:example-one"]),
                    "content_hash": "example-hash-one",
                },
                {
                    "id": "vuln-002",
                    "feed": "example-security-feed",
                    "summary": "Medium prototype pollution advisory",
                    "severity": "MEDIUM",
                    "ecosystem": "npm",
                    "package": "example-package",
                    "affected_ranges": json.dumps(
                        [{"introduced": "1.0.0", "fixed": "1.2.3"}]
                    ),
                    "aliases": json.dumps(["GHSA-example-medium"]),
                    "source_url": "https://security.example.test/GHSA-example-medium",
                    "published_at": "2024-01-01T12:00:00+00:00",
                    "fetched_at": "2024-01-02T12:00:00+00:00",
                    "identities": json.dumps(["sha256:example-two"]),
                    "content_hash": "example-hash-two",
                },
                {
                    "id": "vuln-003",
                    "feed": "example-security-feed",
                    "summary": "High advisory without a publication date",
                    "severity": "HIGH",
                    "ecosystem": "PyPI",
                    "package": "example-legacy-package",
                    "affected_ranges": json.dumps([{"introduced": "0"}]),
                    "aliases": json.dumps(["EXAMPLE-0003"]),
                    "source_url": "https://security.example.test/EXAMPLE-0003",
                    "published_at": None,
                    "fetched_at": "2024-03-01T12:00:00+00:00",
                    "identities": json.dumps(["sha256:example-three"]),
                    "content_hash": "example-hash-three",
                },
            ],
        )

    def override_get_session():
        session = testing_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/vuln-advisories")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert len(payload["items"]) == 3, payload
            assert payload["total"] == 3, payload

            critical_response = client.get(
                "/api/vuln-advisories",
                params={"severity": "CRITICAL"},
            )
            assert critical_response.status_code == 200, critical_response.text
            critical_payload = critical_response.json()
            assert len(critical_payload["items"]) == 1, critical_payload
            assert critical_payload["total"] == 1, critical_payload
            assert critical_payload["items"][0]["severity"] == "CRITICAL"

            page_response = client.get(
                "/api/vuln-advisories",
                params={"limit": 1, "offset": 0},
            )
            assert page_response.status_code == 200, page_response.text
            page_payload = page_response.json()
            assert len(page_payload["items"]) == 1, page_payload
            assert page_payload["total"] == 3, page_payload
            assert page_payload["limit"] == 1, page_payload
            assert page_payload["offset"] == 0, page_payload

            for item in payload["items"]:
                assert item["published_at"] is None or isinstance(
                    item["published_at"], str
                ), item
                assert isinstance(item["aliases"], list), item
                assert isinstance(item["affected_ranges"], list), item
    finally:
        app.dependency_overrides.clear()
        testing_session_factory.close_all()
        test_engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_acceptance_test()