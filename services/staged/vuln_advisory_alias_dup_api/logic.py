import json
from collections import defaultdict

from app.db import get_session
from app.models import VulnAdvisory


def _parse_string_array(value):
    if value is None:
        return []

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    if not isinstance(value, (list, tuple)):
        return []

    return {
        item
        for item in value
        if isinstance(item, str) and item != ""
    }


def get_alias_duplicate_report(session):
    rows = session.query(
        VulnAdvisory.id,
        VulnAdvisory.aliases,
        VulnAdvisory.identities,
    ).all()

    alias_advisories = defaultdict(set)
    identity_advisories = defaultdict(set)
    with_aliases = 0
    with_identities = 0

    for advisory_id, aliases_value, identities_value in rows:
        advisory_id = str(advisory_id) if advisory_id is not None else ""
        aliases = _parse_string_array(aliases_value)
        identities = _parse_string_array(identities_value)

        if aliases:
            with_aliases += 1
            for alias in aliases:
                alias_advisories[alias].add(advisory_id)

        if identities:
            with_identities += 1
            for identity in identities:
                identity_advisories[identity].add(advisory_id)

    duplicate_aliases = [
        (alias, advisory_ids)
        for alias, advisory_ids in alias_advisories.items()
        if len(advisory_ids) > 1
    ]
    duplicate_identities = [
        (identity, advisory_ids)
        for identity, advisory_ids in identity_advisories.items()
        if len(advisory_ids) > 1
    ]

    duplicate_aliases.sort(key=lambda item: (-len(item[1]), item[0]))
    duplicate_identities.sort(key=lambda item: (-len(item[1]), item[0]))

    return {
        "total_advisories": len(rows),
        "with_aliases": with_aliases,
        "with_identities": with_identities,
        "alias_collisions": len(duplicate_aliases),
        "identity_collisions": len(duplicate_identities),
        "top_duplicate_aliases": [
            {
                "alias": alias,
                "advisory_count": len(advisory_ids),
                "example_ids": sorted(advisory_ids),
            }
            for alias, advisory_ids in duplicate_aliases[:25]
        ],
        "top_duplicate_identities": [
            {
                "identity": identity,
                "advisory_count": len(advisory_ids),
                "example_ids": sorted(advisory_ids),
            }
            for identity, advisory_ids in duplicate_identities[:25]
        ],
    }


get_alias_duplicate_summary = get_alias_duplicate_report
get_alias_duplicates = get_alias_duplicate_report
get_duplicate_aliases = get_alias_duplicate_report
get_duplicate_alias_summary = get_alias_duplicate_report
compute_alias_duplicates = get_alias_duplicate_report
summarize_alias_duplicates = get_alias_duplicate_report
build_alias_duplicate_report = get_alias_duplicate_report
build_vuln_advisory_alias_duplicate_report = get_alias_duplicate_report


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import Depends, FastAPI
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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    seed_rows = [
        {
            "id": "ADV-001",
            "feed": "test",
            "summary": "Advisory one",
            "severity": "HIGH",
            "ecosystem": "pypi",
            "package": "example-one",
            "affected_ranges": [],
            "aliases": ["CVE-2024-0001"],
            "source_url": "https://example.invalid/1",
            "published_at": now,
            "fetched_at": now,
            "identities": ["GHSA-xxxx"],
            "content_hash": "test-hash-001",
        },
        {
            "id": "ADV-002",
            "feed": "test",
            "summary": "Advisory two",
            "severity": "HIGH",
            "ecosystem": "pypi",
            "package": "example-two",
            "affected_ranges": [],
            "aliases": ["CVE-2024-0001"],
            "source_url": "https://example.invalid/2",
            "published_at": now,
            "fetched_at": now,
            "identities": ["GHSA-xxxx"],
            "content_hash": "test-hash-002",
        },
        {
            "id": "ADV-003",
            "feed": "test",
            "summary": "Advisory three",
            "severity": "MEDIUM",
            "ecosystem": "npm",
            "package": "example-three",
            "affected_ranges": [],
            "aliases": ["CVE-2024-0002"],
            "source_url": "https://example.invalid/3",
            "published_at": now,
            "fetched_at": now,
            "identities": ["GHSA-yyyy"],
            "content_hash": "test-hash-003",
        },
        {
            "id": "ADV-004",
            "feed": "test",
            "summary": "Advisory four",
            "severity": "LOW",
            "ecosystem": "npm",
            "package": "example-four",
            "affected_ranges": [],
            "aliases": ["CVE-2024-0003"],
            "source_url": "https://example.invalid/4",
            "published_at": now,
            "fetched_at": now,
            "identities": None,
            "content_hash": "test-hash-004",
        },
    ]

    with TestSession() as session:
        session.add_all([VulnAdvisory(**row) for row in seed_rows])
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/vuln/advisory/alias-duplicates")
    def alias_duplicates(session=Depends(get_session)):
        return get_alias_duplicate_report(session)

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/advisory/alias-duplicates")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_advisories"] == 4
        assert payload["with_aliases"] == 4
        assert payload["alias_collisions"] == 1
        assert payload["identity_collisions"] == 1
        assert payload["top_duplicate_aliases"][0]["alias"] == "CVE-2024-0001"
        assert payload["top_duplicate_aliases"][0]["advisory_count"] == 2
        assert set(payload) == {
            "total_advisories",
            "with_aliases",
            "with_identities",
            "alias_collisions",
            "identity_collisions",
            "top_duplicate_aliases",
            "top_duplicate_identities",
        }

    print("PASS")