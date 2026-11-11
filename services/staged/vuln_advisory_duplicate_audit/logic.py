from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _aliases(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return [value]
        return decoded if isinstance(decoded, list) else [decoded]
    return [value]


def _validate_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 100
    return max(1, min(value, 1000))


def get_duplicate_advisories(
    session: Session = Depends(get_session),
    group_by: str = "content_hash",
    limit: int = 100,
) -> dict[str, Any]:
    """Return duplicate content-hash groups and their advisory details."""
    if group_by != "content_hash":
        raise ValueError("group_by must be 'content_hash'")

    limit = _validate_limit(limit)

    grouped_query = (
        select(
            VulnAdvisory.content_hash.label("content_hash"),
            func.count(VulnAdvisory.id).label("n"),
            func.min(VulnAdvisory.published_at).label("first_published"),
            func.max(VulnAdvisory.published_at).label("last_published"),
        )
        .where(VulnAdvisory.content_hash.is_not(None))
        .group_by(VulnAdvisory.content_hash)
        .having(func.count(VulnAdvisory.id) > 1)
        .order_by(func.count(VulnAdvisory.id).desc(), VulnAdvisory.content_hash.asc())
    )

    groups = session.execute(grouped_query).all()

    total_groups = 0
    total_query = (
        select(func.count())
        .select_from(
            grouped_query.with_only_columns(VulnAdvisory.content_hash).subquery()
        )
    )
    total_groups = int(session.execute(total_query).scalar() or 0)

    groups = groups[:limit]
    hashes = [row.content_hash for row in groups]

    detail_rows: list[Any] = []
    if hashes:
        detail_query = (
            select(
                VulnAdvisory.id,
                VulnAdvisory.content_hash,
                VulnAdvisory.feed,
                VulnAdvisory.severity,
                VulnAdvisory.ecosystem,
                VulnAdvisory.package,
                VulnAdvisory.published_at,
                VulnAdvisory.aliases,
            )
            .where(VulnAdvisory.content_hash.in_(hashes))
            .order_by(
                VulnAdvisory.content_hash.asc(),
                VulnAdvisory.published_at.asc(),
                VulnAdvisory.id.asc(),
            )
        )
        detail_rows = list(session.execute(detail_query).all())

    details_by_hash: dict[str, list[dict[str, Any]]] = {}
    for row in detail_rows:
        details_by_hash.setdefault(str(row.content_hash), []).append(
            {
                "id": str(row.id),
                "feed": str(row.feed) if row.feed is not None else None,
                "severity": row.severity,
                "ecosystem": row.ecosystem,
                "package": row.package,
                "published_at": _timestamp(row.published_at),
                "aliases": _aliases(row.aliases),
            }
        )

    result_groups: list[dict[str, Any]] = []
    for row in groups:
        content_hash = str(row.content_hash)
        advisories = details_by_hash.get(content_hash, [])
        result_groups.append(
            {
                "content_hash": content_hash,
                "count": int(row.n),
                "first_published": _timestamp(row.first_published),
                "last_published": _timestamp(row.last_published),
                "advisory_ids": [advisory["id"] for advisory in advisories],
                "feeds": [
                    advisory["feed"]
                    for advisory in advisories
                    if advisory["feed"] is not None
                ],
                "advisories": advisories,
            }
        )

    return {
        "limit": limit,
        "group_by": "content_hash",
        "duplicate_groups": result_groups,
        "total_groups": total_groups,
    }


def audit_vuln_advisory_duplicates(
    session: Session = Depends(get_session),
    group_by: str = "content_hash",
    limit: int = 100,
) -> dict[str, Any]:
    return get_duplicate_advisories(session, group_by=group_by, limit=limit)


def get_vuln_advisory_duplicates(
    session: Session = Depends(get_session),
    group_by: str = "content_hash",
    limit: int = 100,
) -> dict[str, Any]:
    return get_duplicate_advisories(session, group_by=group_by, limit=limit)


def get_advisory_duplicates(
    session: Session = Depends(get_session),
    group_by: str = "content_hash",
    limit: int = 100,
) -> dict[str, Any]:
    return get_duplicate_advisories(session, group_by=group_by, limit=limit)


def duplicate_advisory_audit(
    session: Session = Depends(get_session),
    group_by: str = "content_hash",
    limit: int = 100,
) -> dict[str, Any]:
    return get_duplicate_advisories(session, group_by=group_by, limit=limit)


def _self_test() -> None:
    from fastapi import FastAPI
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

    shared_hash = "shared-content-hash"
    rows = [
        ("adv-1", "feed-a", shared_hash, "high", "npm", "pkg-a", ["CVE-1"]),
        ("adv-2", "feed-b", shared_hash, "critical", "npm", "pkg-b", ["CVE-1"]),
        ("adv-3", "feed-c", shared_hash, "medium", "pypi", "pkg-c", ["CVE-1"]),
        ("adv-4", "feed-a", "unique-hash-1", "low", "npm", "pkg-d", []),
        ("adv-5", "feed-b", "unique-hash-2", "low", "npm", "pkg-e", []),
        ("adv-6", "feed-c", "unique-hash-3", "low", "npm", "pkg-f", []),
    ]
    for advisory_id, feed, content_hash, severity, ecosystem, package, aliases in rows:
        testing_session.add(
            VulnAdvisory(
                id=advisory_id,
                feed=feed,
                source_url=f"https://example.test/{advisory_id}",
                content_hash=content_hash,
                severity=severity,
                ecosystem=ecosystem,
                package=package,
                published_at=datetime(2024, 1, 1),
                aliases=aliases,
            )
        )
    testing_session.commit()

    local_app = FastAPI()

    @local_app.get("/vuln/advisories/duplicates")
    def duplicates(
        group_by: str = "content_hash",
        limit: int = 100,
        session: Session = Depends(get_session),
    ):
        return get_duplicate_advisories(session, group_by=group_by, limit=limit)

    def override_session():
        yield testing_session

    local_app.dependency_overrides[get_session] = override_session
    client = TestClient(local_app)
    response = client.get(
        "/vuln/advisories/duplicates",
        params={"group_by": "content_hash"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["duplicate_groups"]) == 1
    group = payload["duplicate_groups"][0]
    assert group["count"] == 3
    assert len(group["advisory_ids"]) == 3
    assert payload["total_groups"] == 1
    print("PASS")


if __name__ == "__main__":
    _self_test()