from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from app.db import get_session
from app.models import VulnAdvisory


router = APIRouter(prefix="/api")


_SEVERITY_RANK = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "moderate": 3,
    "low": 2,
    "info": 1,
    "informational": 1,
    "none": 0,
    "unknown": 0,
}


def _as_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return [value]
        return _as_values(decoded)
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _identity_text(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("value", "identity", "name", "alias", "id"):
            if value.get(key) is not None:
                value = value[key]
                break
        else:
            return None
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return " ".join(text.split())


def _normalized_identity(value: Any) -> str | None:
    text = _identity_text(value)
    if text is None:
        return None
    normalized = text.casefold()
    if normalized.startswith("cve-"):
        return "cve-" + normalized[4:]
    return normalized


def _display_identity(value: str) -> str:
    if value.startswith("cve-"):
        return "CVE-" + value[4:].upper()
    return value


def _severity_rank(value: Any) -> int:
    if value is None:
        return 0
    return _SEVERITY_RANK.get(str(value).strip().casefold(), 0)


def _max_severity(values: list[Any]) -> str | None:
    if not values:
        return None
    return max(
        (str(value).strip() for value in values if value is not None and str(value).strip()),
        key=_severity_rank,
        default=None,
    )


def _iso_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        result = str(value)
    return result


def compute_identity_overlap(
    session: Any,
    ecosystem: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    query = session.query(VulnAdvisory)
    if ecosystem:
        query = query.filter(VulnAdvisory.ecosystem == ecosystem)
    advisories = query.order_by(VulnAdvisory.id.asc()).all()

    ecosystem_counts: list[dict[str, Any]] = []
    counts_by_ecosystem: dict[str, int] = defaultdict(int)
    packages_by_ecosystem: dict[str, set[str]] = defaultdict(set)
    empty_identity_advisories = 0

    groups: dict[str, dict[str, Any]] = {}
    duplicate_ids: set[Any] = set()

    for advisory in advisories:
        advisory_ecosystem = advisory.ecosystem or ""
        counts_by_ecosystem[advisory_ecosystem] += 1
        package = _identity_text(advisory.package)
        if package:
            packages_by_ecosystem[advisory_ecosystem].add(package)

        aliases = _as_values(advisory.aliases)
        identities = _as_values(advisory.identities)
        if not aliases and not identities:
            empty_identity_advisories += 1

        advisory_identities: set[str] = set()
        for value in list(aliases) + list(identities):
            normalized = _normalized_identity(value)
            if normalized:
                advisory_identities.add(normalized)

        for identity in advisory_identities:
            group = groups.setdefault(
                identity,
                {
                    "feeds": set(),
                    "advisory_ids": set(),
                    "severities": [],
                    "published_at": [],
                },
            )
            if advisory.feed:
                group["feeds"].add(str(advisory.feed))
            group["advisory_ids"].add(advisory.id)
            if advisory.severity:
                group["severities"].append(advisory.severity)
            if advisory.published_at is not None:
                group["published_at"].append(advisory.published_at)

    for ecosystem_name in sorted(counts_by_ecosystem):
        ecosystem_counts.append(
            {
                "ecosystem": ecosystem_name,
                "count": counts_by_ecosystem[ecosystem_name],
                "distinct_packages": len(packages_by_ecosystem[ecosystem_name]),
                "distinct_package_count": len(packages_by_ecosystem[ecosystem_name]),
            }
        )

    qualifying_groups: list[dict[str, Any]] = []
    for identity, group in groups.items():
        feeds = sorted(group["feeds"])
        if len(feeds) < 2:
            continue
        advisory_ids = sorted(group["advisory_ids"], key=lambda item: str(item))
        duplicate_ids.update(advisory_ids)
        published = group["published_at"]
        earliest = min(published) if published else None
        qualifying_groups.append(
            {
                "identity": _display_identity(identity),
                "feeds": feeds,
                "advisory_ids": advisory_ids,
                "max_severity": _max_severity(group["severities"]),
                "earliest_published_at": _iso_datetime(earliest),
            }
        )

    qualifying_groups.sort(key=lambda item: (item["identity"], item["feeds"]))
    limited_groups = qualifying_groups[: max(0, int(limit))]

    return {
        "ecosystem": ecosystem,
        "groups": limited_groups,
        "total_groups": len(qualifying_groups),
        "duplicate_advisories": len(duplicate_ids),
        "ecosystem_counts": ecosystem_counts,
        "empty_identity_advisories": empty_identity_advisories,
        "empty_identity_count": empty_identity_advisories,
        "empty_bucket_count": empty_identity_advisories,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_identity_overlap_data(
    session: Any,
    ecosystem: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    return compute_identity_overlap(session=session, ecosystem=ecosystem, limit=limit)


def get_identity_overlap(
    session: Any = Depends(get_session),
    ecosystem: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    return compute_identity_overlap(session=session, ecosystem=ecosystem, limit=limit)


@router.get("/vuln/advisories/identity-overlap")
def identity_overlap_endpoint(
    ecosystem: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    session: Any = Depends(get_session),
) -> dict[str, Any]:
    return compute_identity_overlap(session=session, ecosystem=ecosystem, limit=limit)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    test_session.add_all(
        [
            VulnAdvisory(
                id=1,
                feed="feed-a",
                summary="first",
                severity="high",
                ecosystem="npm",
                package="example-a",
                aliases=["CVE-TEST-0001"],
                identities=[],
                published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                fetched_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                source_url="https://example.test/a",
            ),
            VulnAdvisory(
                id=2,
                feed="feed-b",
                summary="second",
                severity="critical",
                ecosystem="npm",
                package="example-b",
                aliases=["CVE-TEST-0001"],
                identities=[],
                published_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
                fetched_at=datetime(2024, 1, 4, tzinfo=timezone.utc),
                source_url="https://example.test/b",
            ),
            VulnAdvisory(
                id=3,
                feed="feed-c",
                summary="empty",
                severity="low",
                ecosystem="npm",
                package="example-c",
                aliases=[],
                identities=[],
                published_at=datetime(2024, 1, 5, tzinfo=timezone.utc),
                fetched_at=datetime(2024, 1, 6, tzinfo=timezone.utc),
                source_url="https://example.test/c",
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_session
    response = TestClient(test_app).get(
        "/api/vuln/advisories/identity-overlap",
        params={"ecosystem": "npm", "limit": 50},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_groups"] == 1
    assert len(payload["groups"][0]["feeds"]) == 2
    assert payload["duplicate_advisories"] == 2
    assert payload["empty_identity_advisories"] == 1
    print("PASS")