from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


router = APIRouter(prefix="/api")


def _json_strings(value: Any) -> list[str]:
    """Return strings contained in a defensively parsed JSON value."""
    if value is None:
        return []

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return [value]
        return _json_strings(parsed)

    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            result.extend(_json_strings(item))
        return result

    if isinstance(value, dict):
        result = []
        for item in value.values():
            result.extend(_json_strings(item))
        return result

    return []


def _published_at(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def lookup_advisory(
    value: str,
    limit: int = 25,
    db: Optional[Session] = None,
) -> dict:
    """Resolve an advisory alias, identity, or package name."""
    if not isinstance(value, str) or not 1 <= len(value) <= 200:
        raise ValueError("value must contain between 1 and 200 characters")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    owned_session = None
    if db is None:
        session_context = get_session()
        db = next(session_context)
        owned_session = session_context

    try:
        rows = db.execute(select(VulnAdvisory)).scalars().all()
        needle = value.casefold()
        matches: list[dict[str, Any]] = []

        for advisory in rows:
            aliases = _json_strings(advisory.aliases)
            identities = _json_strings(advisory.identities)

            matched_on: Optional[str] = None
            if any(item.casefold() == needle for item in aliases):
                matched_on = "alias"
            elif any(item.casefold() == needle for item in identities):
                matched_on = "identity"
            elif (
                advisory.package is not None
                and str(advisory.package).casefold() == needle
            ):
                matched_on = "package"

            if matched_on is None:
                continue

            matches.append(
                {
                    "id": str(advisory.id),
                    "feed": advisory.feed,
                    "package": advisory.package,
                    "ecosystem": advisory.ecosystem,
                    "severity": advisory.severity,
                    "published_at": _published_at(advisory.published_at),
                    "source_url": advisory.source_url,
                    "matched_on": matched_on,
                }
            )

            if len(matches) >= limit:
                break

        return {
            "value": value,
            "count": len(matches),
            "matches": matches,
        }
    finally:
        if owned_session is not None:
            try:
                owned_session.close()
            except Exception:
                pass


@router.get("/vuln/advisories/lookup")
def lookup_advisory_endpoint(
    value: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_session),
) -> dict:
    return lookup_advisory(value=value, limit=limit, db=db)


def _self_test() -> None:
    from datetime import datetime, timezone

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
    testing_session = sessionmaker(bind=engine)()

    testing_session.add_all(
        [
            VulnAdvisory(
                id="advisory-alias",
                feed="test-feed",
                summary="Alias test advisory",
                severity="high",
                ecosystem="pypi",
                package="example-package",
                affected_ranges=[],
                aliases=["GHSA-XXXX"],
                identities=["EXAMPLE-1"],
                source_url="https://example.invalid/alias",
                published_at=datetime.now(timezone.utc),
            ),
            VulnAdvisory(
                id="advisory-package",
                feed="test-feed",
                summary="Package test advisory",
                severity="medium",
                ecosystem="pypi",
                package="requests",
                affected_ranges=[],
                aliases=[],
                identities=[],
                source_url="https://example.invalid/package",
                published_at=datetime.now(timezone.utc),
            ),
        ]
    )
    testing_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: testing_session

    client = TestClient(test_app)

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
        params={"value": "not-a-real-advisory"},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 0

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()