from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


router = APIRouter(prefix="/api")


def _json_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, (list, tuple, set)):
        return []
    return [item for item in value if isinstance(item, str)]


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def lookup_advisory(
    value: str,
    limit: int = 25,
    session: Session | None = None,
) -> dict:
    if not isinstance(value, str) or not 1 <= len(value) <= 200:
        raise ValueError("value must contain between 1 and 200 characters")
    if not 1 <= int(limit) <= 100:
        raise ValueError("limit must be between 1 and 100")

    if session is None:
        dependency_session = get_session()
        if hasattr(dependency_session, "__next__"):
            session = next(dependency_session)
        else:
            session = dependency_session

    needle = value.casefold()
    rows = session.execute(select(VulnAdvisory)).scalars().all()
    matches: list[dict] = []

    for advisory in rows:
        aliases = {item.casefold() for item in _json_values(advisory.aliases)}
        identities = {item.casefold() for item in _json_values(advisory.identities)}
        package = advisory.package

        if needle in aliases:
            matched_on = "alias"
        elif needle in identities:
            matched_on = "identity"
        elif package is not None and package == value:
            matched_on = "package"
        else:
            continue

        matches.append(
            {
                "id": str(advisory.id),
                "feed": advisory.feed,
                "package": advisory.package,
                "ecosystem": advisory.ecosystem,
                "severity": advisory.severity,
                "published_at": _isoformat(advisory.published_at),
                "source_url": advisory.source_url,
                "matched_on": matched_on,
            }
        )

    matches.sort(key=lambda item: (item["published_at"] is None, item["published_at"] or "", item["id"]))
    return {
        "value": value,
        "count": len(matches[: int(limit)]),
        "matches": matches[: int(limit)],
    }


@router.get("/vuln/advisories/lookup")
def lookup_endpoint(
    value: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(25, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict:
    return lookup_advisory(value=value, limit=limit, session=session)


app = FastAPI()
app.include_router(router)


def main() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    with SessionLocal() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id="11111111-1111-1111-1111-111111111111",
                    feed="test-feed",
                    summary="Alias test advisory",
                    severity="HIGH",
                    ecosystem="PyPI",
                    package=None,
                    affected_ranges=[],
                    aliases=["GHSA-xxxx"],
                    identities=[],
                    source_url="https://example.invalid/advisory/1",
                    published_at=datetime(2024, 1, 1, tzinfo=None),
                ),
                VulnAdvisory(
                    id="22222222-2222-2222-2222-222222222222",
                    feed="test-feed",
                    summary="Package test advisory",
                    severity="MEDIUM",
                    ecosystem="PyPI",
                    package="requests",
                    affected_ranges=[],
                    aliases=[],
                    identities=[],
                    source_url="https://example.invalid/advisory/2",
                    published_at=datetime(2024, 1, 2, tzinfo=None),
                ),
            ]
        )
        session.commit()

    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/vuln/advisories/lookup",
                params={"value": "ghsa-XXXX"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["count"] == 1
            assert payload["matches"][0]["matched_on"] == "alias"

            response = client.get(
                "/api/vuln/advisories/lookup",
                params={"value": "requests"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["count"] == 1
            assert payload["matches"][0]["matched_on"] == "package"

            response = client.get(
                "/api/vuln/advisories/lookup",
                params={"value": "this-value-does-not-exist"},
            )
            assert response.status_code == 200
            assert response.json()["count"] == 0
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    main()