from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory


class IdentityOverlapGroup(BaseModel):
    identity: str
    feeds: List[str]
    advisory_ids: List[int]
    max_severity: Optional[str] = None
    earliest_published_at: Optional[str] = None


class EcosystemCount(BaseModel):
    ecosystem: str
    count: int
    distinct_package_count: int
    empty_identity_count: int


class IdentityOverlapResponse(BaseModel):
    ecosystem: str
    groups: List[IdentityOverlapGroup]
    total_groups: int
    duplicate_advisories: int
    ecosystem_counts: List[EcosystemCount]
    empty_identity_advisories: int
    empty_identity_count: int
    generated_at: str


router = APIRouter(prefix="/api", tags=["vulnerability-advisories"])


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return [value]
        return decoded if isinstance(decoded, list) else [decoded]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _identity_values(value: Any) -> Iterable[str]:
    for item in _as_list(value):
        if isinstance(item, dict):
            item = item.get("value", item.get("id", item.get("identity")))
        if item is None:
            continue
        normalized = str(item).strip().upper()
        if normalized:
            yield normalized


def _severity_rank(value: Optional[str]) -> int:
    return {
        "critical": 4,
        "high": 3,
        "medium": 2,
        "moderate": 2,
        "low": 1,
        "info": 0,
        "informational": 0,
        "none": 0,
        "unknown": 0,
    }.get((value or "unknown").strip().lower(), 0)


def _severity_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value.strip().lower()


def _isoformat(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _compute_identity_overlap(
    session: Session,
    ecosystem: str,
    limit: int,
) -> IdentityOverlapResponse:
    advisories = list(session.execute(select(VulnAdvisory)).scalars().all())

    grouped: Dict[str, List[VulnAdvisory]] = {}
    for advisory in advisories:
        if (advisory.ecosystem or "").strip().lower() != ecosystem.strip().lower():
            continue
        values = set(_identity_values(advisory.aliases))
        values.update(_identity_values(advisory.identities))
        for value in values:
            grouped.setdefault(value, []).append(advisory)

    groups: List[IdentityOverlapGroup] = []
    participating_ids = set()
    for identity, members in grouped.items():
        feeds = sorted({str(advisory.feed) for advisory in members if advisory.feed})
        if len(feeds) < 2:
            continue

        advisory_ids = sorted({int(advisory.id) for advisory in members})
        participating_ids.update(advisory_ids)

        severity = None
        severity_rank = -1
        for advisory in members:
            candidate_rank = _severity_rank(advisory.severity)
            if candidate_rank > severity_rank:
                severity_rank = candidate_rank
                severity = _severity_value(advisory.severity)

        published_values = [
            _isoformat(advisory.published_at)
            for advisory in members
            if advisory.published_at is not None
        ]
        earliest = min(published_values) if published_values else None

        groups.append(
            IdentityOverlapGroup(
                identity=identity,
                feeds=feeds,
                advisory_ids=advisory_ids,
                max_severity=severity,
                earliest_published_at=earliest,
            )
        )

    groups.sort(key=lambda group: (group.identity, group.advisory_ids))
    groups = groups[:limit]

    counts_by_ecosystem: Dict[str, Dict[str, Any]] = {}
    empty_total = 0
    for advisory in advisories:
        name = (advisory.ecosystem or "unknown").strip().lower() or "unknown"
        bucket = counts_by_ecosystem.setdefault(
            name,
            {
                "count": 0,
                "packages": set(),
                "empty": 0,
            },
        )
        bucket["count"] += 1
        if advisory.package is not None:
            bucket["packages"].add(str(advisory.package))

        aliases_empty = not list(_identity_values(advisory.aliases))
        identities_empty = not list(_identity_values(advisory.identities))
        if aliases_empty and identities_empty:
            bucket["empty"] += 1
            empty_total += 1

    ecosystem_counts = [
        EcosystemCount(
            ecosystem=name,
            count=int(bucket["count"]),
            distinct_package_count=len(bucket["packages"]),
            empty_identity_count=int(bucket["empty"]),
        )
        for name, bucket in sorted(counts_by_ecosystem.items())
    ]

    return IdentityOverlapResponse(
        ecosystem=ecosystem,
        groups=groups,
        total_groups=len(groups),
        duplicate_advisories=len(participating_ids),
        ecosystem_counts=ecosystem_counts,
        empty_identity_advisories=empty_total,
        empty_identity_count=empty_total,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/vuln/advisories/identity-overlap",
    response_model=IdentityOverlapResponse,
)
def identity_overlap(
    ecosystem: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> IdentityOverlapResponse:
    return _compute_identity_overlap(session, ecosystem, limit)


app = FastAPI(title="Vulnerability Advisory Identity Overlap API")
app.include_router(router)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)

    from sqlalchemy.orm import sessionmaker

    session_factory = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)

    with session_factory() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id=1001,
                    feed="feed-a",
                    summary="First test advisory",
                    severity="high",
                    ecosystem="npm",
                    package="example-package",
                    aliases=["CVE-TEST-0001"],
                    identities=[],
                    published_at=now,
                    fetched_at=now,
                    source_url="https://example.test/1001",
                    content_hash="test-1001",
                    affected_ranges=[],
                ),
                VulnAdvisory(
                    id=1002,
                    feed="feed-b",
                    summary="Second test advisory",
                    severity="medium",
                    ecosystem="npm",
                    package="example-package",
                    aliases=["CVE-TEST-0001"],
                    identities=[],
                    published_at=now,
                    fetched_at=now,
                    source_url="https://example.test/1002",
                    content_hash="test-1002",
                    affected_ranges=[],
                ),
                VulnAdvisory(
                    id=1003,
                    feed="feed-c",
                    summary="Advisory without identities",
                    severity="low",
                    ecosystem="npm",
                    package="other-package",
                    aliases=[],
                    identities=[],
                    published_at=now,
                    fetched_at=now,
                    source_url="https://example.test/1003",
                    content_hash="test-1003",
                    affected_ranges=[],
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/vuln/advisories/identity-overlap",
            params={"ecosystem": "npm", "limit": 10},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_groups"] == 1, payload
        assert len(payload["groups"][0]["feeds"]) == 2, payload
        assert payload["duplicate_advisories"] == 2, payload
        assert payload["empty_identity_advisories"] == 1, payload
        assert payload["empty_identity_count"] == 1, payload
        assert payload["ecosystem_counts"][0]["empty_identity_count"] == 1, payload

    print("PASS")


if __name__ == "__main__":
    _run_self_test()