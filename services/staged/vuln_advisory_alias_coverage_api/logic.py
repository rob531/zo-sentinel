from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory


def _has_aliases(aliases: Any) -> bool:
    if aliases is None:
        return False
    if isinstance(aliases, str):
        return bool(aliases.strip())
    if isinstance(aliases, (list, tuple, dict, set)):
        return bool(aliases)
    return bool(aliases)


def _group_sort_key(value: Any) -> tuple[bool, str]:
    if value is None:
        return True, ""
    return False, str(value)


def compute_alias_coverage(advisories: list[dict]) -> dict:
    total = len(advisories)
    with_aliases = sum(
        1 for advisory in advisories if _has_aliases(advisory.get("aliases"))
    )

    feed_counts: dict[Any, dict[str, int]] = {}
    severity_counts: dict[Any, dict[str, int]] = {}

    for advisory in advisories:
        feed = advisory.get("feed")
        severity = advisory.get("severity")
        has_aliases = int(_has_aliases(advisory.get("aliases")))

        feed_group = feed_counts.setdefault(
            feed, {"total": 0, "with_aliases": 0}
        )
        feed_group["total"] += 1
        feed_group["with_aliases"] += has_aliases

        severity_group = severity_counts.setdefault(
            severity, {"total": 0, "with_aliases": 0}
        )
        severity_group["total"] += 1
        severity_group["with_aliases"] += has_aliases

    return {
        "total": total,
        "with_aliases": with_aliases,
        "coverage_ratio": round(with_aliases / total, 3) if total else 0.0,
        "by_feed": [
            {
                "feed": feed,
                "total": counts["total"],
                "with_aliases": counts["with_aliases"],
            }
            for feed, counts in sorted(
                feed_counts.items(), key=lambda item: _group_sort_key(item[0])
            )
        ],
        "by_severity": [
            {
                "severity": severity,
                "total": counts["total"],
                "with_aliases": counts["with_aliases"],
            }
            for severity, counts in sorted(
                severity_counts.items(), key=lambda item: _group_sort_key(item[0])
            )
        ],
    }


def load_advisories(session: Session) -> list[dict]:
    return [
        {
            "feed": advisory.feed,
            "severity": advisory.severity,
            "aliases": advisory.aliases,
        }
        for advisory in session.query(VulnAdvisory).all()
    ]


def _self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = test_session_factory()

    published_at = datetime(2024, 1, 1, 0, 0, 0)
    session.add_all(
        [
            VulnAdvisory(
                id=1,
                feed="osv",
                summary="Advisory one",
                severity="HIGH",
                ecosystem="PyPI",
                package="example-one",
                affected_ranges=[],
                aliases=["CVE-2024-0001"],
                source_url="https://example.test/1",
                published_at=published_at,
                fetched_at=published_at,
                identities=[],
                content_hash="hash-1",
            ),
            VulnAdvisory(
                id=2,
                feed="osv",
                summary="Advisory two",
                severity="LOW",
                ecosystem="PyPI",
                package="example-two",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.test/2",
                published_at=published_at,
                fetched_at=published_at,
                identities=[],
                content_hash="hash-2",
            ),
            VulnAdvisory(
                id=3,
                feed="github",
                summary="Advisory three",
                severity="HIGH",
                ecosystem="npm",
                package="example-three",
                affected_ranges=[],
                aliases=["GHSA-test-0003"],
                source_url="https://example.test/3",
                published_at=published_at,
                fetched_at=published_at,
                identities=[],
                content_hash="hash-3",
            ),
            VulnAdvisory(
                id=4,
                feed="github",
                summary="Advisory four",
                severity="MEDIUM",
                ecosystem="npm",
                package="example-four",
                affected_ranges=[],
                aliases=None,
                source_url="https://example.test/4",
                published_at=published_at,
                fetched_at=published_at,
                identities=[],
                content_hash="hash-4",
            ),
        ]
    )
    session.commit()

    test_app = FastAPI()

    @test_app.get("/api/vuln/advisories/alias-coverage")
    def alias_coverage_route(db: Session = Depends(get_session)):
        return compute_alias_coverage(load_advisories(db))

    def override_get_session():
        yield session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/vuln/advisories/alias-coverage")
            assert response.status_code == 200
            result = response.json()
            assert result["total"] == 4
            assert result["with_aliases"] == 2
            assert result["coverage_ratio"] == 0.5
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()