from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory

from .logic import lookup_advisory

router = APIRouter(prefix="/api", tags=["vulnerability-advisories"])


def _run_lookup(value: str, limit: int, db: Any) -> dict:
    parameters = inspect.signature(lookup_advisory).parameters
    for parameter_name in ("db", "session"):
        if parameter_name in parameters:
            return lookup_advisory(
                value=value,
                limit=limit,
                **{parameter_name: db},
            )

    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    if len(positional) >= 3:
        return lookup_advisory(value, limit, db)

    return lookup_advisory(value, limit)


@router.get("/vuln/advisories/lookup")
def lookup_advisory_endpoint(
    value: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(25, ge=1, le=100),
    db: Any = Depends(get_session),
) -> dict:
    return _run_lookup(value, limit, db)


def _main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def make_row(
        *,
        identifier: str,
        package: str,
        aliases: list[str],
        identities: list[str],
        content_hash: str,
    ) -> VulnAdvisory:
        now = datetime.now(timezone.utc)
        values: dict[str, Any] = {
            "affected_ranges": [],
            "aliases": aliases,
            "content_hash": content_hash,
            "ecosystem": "PyPI",
            "feed": "test-feed",
            "fetched_at": now,
            "id": identifier,
            "identities": identities,
            "package": package,
            "published_at": now,
            "severity": "HIGH",
            "source_url": f"https://example.test/advisories/{identifier}",
            "summary": f"Test advisory for {package}",
        }
        for column in VulnAdvisory.__table__.columns:
            if column.name == "id" and column.type.python_type is UUID:
                values[column.name] = UUID(identifier)
        return VulnAdvisory(**values)

    with testing_session() as session:
        session.add(
            make_row(
                identifier="00000000-0000-0000-0000-000000000001",
                package="urllib3",
                aliases=["GHSA-xxxx"],
                identities=["CVE-2024-0001"],
                content_hash="test-advisory-hash-1",
            )
        )
        session.add(
            make_row(
                identifier="00000000-0000-0000-0000-000000000002",
                package="requests",
                aliases=[],
                identities=[],
                content_hash="test-advisory-hash-2",
            )
        )
        session.commit()

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session():
        with testing_session() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(local_app)

    alias_response = client.get(
        "/api/vuln/advisories/lookup",
        params={"value": "ghsa-XXXX"},
    )
    assert alias_response.status_code == 200, alias_response.text
    alias_payload = alias_response.json()
    assert alias_payload["count"] == 1, alias_payload
    assert alias_payload["matches"][0]["matched_on"] == "alias", alias_payload

    package_response = client.get(
        "/api/vuln/advisories/lookup",
        params={"value": "requests"},
    )
    assert package_response.status_code == 200, package_response.text
    package_payload = package_response.json()
    assert package_payload["count"] == 1, package_payload
    assert package_payload["matches"][0]["matched_on"] == "package", package_payload

    empty_response = client.get(
        "/api/vuln/advisories/lookup",
        params={"value": "not-a-real-advisory-identifier"},
    )
    assert empty_response.status_code == 200, empty_response.text
    assert empty_response.json()["count"] == 0, empty_response.text

    print("PASS")


if __name__ == "__main__":
    _main()