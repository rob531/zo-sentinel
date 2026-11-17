from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from fastapi import Depends, FastAPI
from app.db import get_session
from app.models import VulnAdvisory


def _bucket(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    text = str(value).strip()
    return text or "UNKNOWN"


def _is_missing_aliases(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, set)):
        return len(value) == 0
    if isinstance(value, str):
        return not value.strip() or value.strip() == "[]"
    return False


def _is_missing_package(value: Any) -> bool:
    return value is None or not str(value).strip()


def get_severity_mix(session: Any, days: int = 30) -> dict[str, Any]:
    """Return advisory severity, ecosystem, and feed distributions."""
    if isinstance(days, bool) or not isinstance(days, int):
        raise ValueError("days must be an integer")
    if days < 1:
        raise ValueError("days must be at least 1")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        session.query(
            VulnAdvisory.severity,
            VulnAdvisory.ecosystem,
            VulnAdvisory.feed,
            VulnAdvisory.aliases,
            VulnAdvisory.package,
        )
        .filter(VulnAdvisory.published_at >= cutoff)
        .all()
    )

    by_severity: dict[str, int] = {}
    by_ecosystem: dict[str, int] = {}
    by_feed: dict[str, int] = {}
    missing_aliases = 0
    missing_package = 0

    for severity, ecosystem, feed, aliases, package in rows:
        severity_key = _bucket(severity)
        ecosystem_key = _bucket(ecosystem)
        feed_key = _bucket(feed)

        by_severity[severity_key] = by_severity.get(severity_key, 0) + 1
        by_ecosystem[ecosystem_key] = by_ecosystem.get(ecosystem_key, 0) + 1
        by_feed[feed_key] = by_feed.get(feed_key, 0) + 1

        if _is_missing_aliases(aliases):
            missing_aliases += 1
        if _is_missing_package(package):
            missing_package += 1

    return {
        "by_severity": by_severity,
        "by_ecosystem": by_ecosystem,
        "by_feed": by_feed,
        "missing_aliases": missing_aliases,
        "missing_package": missing_package,
        "total_advisories": len(rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def compute_severity_mix(days: int = 30, session: Any = None) -> dict[str, Any]:
    if session is None:
        raise ValueError("session is required")
    return get_severity_mix(session=session, days=days)


def get_advisory_severity_mix(session: Any, days: int = 30) -> dict[str, Any]:
    return get_severity_mix(session=session, days=days)


advisory_severity_mix = get_severity_mix


if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    seed_rows = [
        VulnAdvisory(
            feed="osv",
            summary="one",
            severity="CRITICAL",
            ecosystem="npm",
            package="alpha",
            affected_ranges=[],
            aliases=["CVE-2025-0001"],
            source_url="https://example.invalid/1",
            published_at=now - timedelta(days=1),
            fetched_at=now,
        ),
        VulnAdvisory(
            feed="osv",
            summary="two",
            severity="HIGH",
            ecosystem="npm",
            package="beta",
            affected_ranges=[],
            aliases=[],
            source_url="https://example.invalid/2",
            published_at=now - timedelta(days=2),
            fetched_at=now,
        ),
        VulnAdvisory(
            feed="nvd",
            summary="three",
            severity="MEDIUM",
            ecosystem="PyPI",
            package=None,
            affected_ranges=[],
            aliases=["CVE-2025-0003"],
            source_url="https://example.invalid/3",
            published_at=now - timedelta(days=3),
            fetched_at=now,
        ),
        VulnAdvisory(
            feed="nvd",
            summary="four",
            severity="LOW",
            ecosystem="Go",
            package="gamma",
            affected_ranges=[],
            aliases=["CVE-2025-0004"],
            source_url="https://example.invalid/4",
            published_at=now - timedelta(days=4),
            fetched_at=now,
        ),
        VulnAdvisory(
            feed="osv",
            summary="five",
            severity="CRITICAL",
            ecosystem="npm",
            package="delta",
            affected_ranges=[],
            aliases=["CVE-2025-0005"],
            source_url="https://example.invalid/5",
            published_at=now - timedelta(days=5),
            fetched_at=now,
        ),
    ]

    class _Query:
        def __init__(self, rows: Iterable[Any]):
            self.rows = list(rows)

        def filter(self, *_args: Any) -> "_Query":
            return self

        def all(self) -> list[Any]:
            return [
                (
                    row.severity,
                    row.ecosystem,
                    row.feed,
                    row.aliases,
                    row.package,
                )
                for row in self.rows
            ]

    class _Session:
        def query(self, *_entities: Any) -> _Query:
            return _Query(seed_rows)

    session = _Session()
    test_app = FastAPI()

    @test_app.get("/api/vuln/advisories/severity-mix")
    def _severity_mix_endpoint(
        days: int = 30,
        db_session: Any = Depends(get_session),
    ) -> dict[str, Any]:
        return get_severity_mix(session=db_session, days=days)

    test_app.dependency_overrides[get_session] = lambda: session

    result = get_severity_mix(session=session, days=30)
    assert result["total_advisories"] == 5
    assert result["by_severity"] == {"CRITICAL": 2, "HIGH": 1, "MEDIUM": 1, "LOW": 1}
    assert result["by_ecosystem"] == {"npm": 3, "PyPI": 1, "Go": 1}
    assert result["by_feed"] == {"osv": 3, "nvd": 2}
    assert result["missing_aliases"] == 1
    assert result["missing_package"] == 1
    print("PASS")