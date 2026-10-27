"""vuln_advisory_severity_ecology_report.py -- advisory corpus severity/ecology report.

Summarises the CVE advisory corpus (vuln_advisories) by severity, ecosystem and
feed, and how many distinct advisories link to registry servers via vuln_links.

The report builder is a PURE function over plain row dicts (stdlib only) so it is
testable offline; the DB seam is the repo's real SQLAlchemy session
(app.db.get_session) over the real app models (app.models.VulnAdvisory /
app.models.VulnLink). Read-only: no network, no DB writes.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter(prefix="/api/vuln-report", tags=["vuln-report"])

TOP_PACKAGE_LIMIT = 20


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm(value: Any, default: str = "unknown") -> str:
    text = _text(value).lower()
    return text or default


def build_report(advisories: Sequence[Dict[str, Any]], links: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the severity/ecology report from plain row dicts. Pure function."""
    advisory_rows = list(advisories or [])
    link_rows = list(links or [])

    by_severity: Counter = Counter()
    by_ecosystem: Counter = Counter()
    by_feed: Counter = Counter()
    package_counts: Counter = Counter()

    advisory_keys: List[str] = []
    seen_keys = set()

    for index, row in enumerate(advisory_rows):
        row = row or {}
        advisory_id = _text(row.get("id"))
        key = advisory_id or f"__row_{index}"
        if key not in seen_keys:
            seen_keys.add(key)
            advisory_keys.append(key)

        by_severity[_norm(row.get("severity"))] += 1
        by_ecosystem[_norm(row.get("ecosystem"))] += 1
        by_feed[_norm(row.get("feed"))] += 1

        package = _text(row.get("package"))
        if package:
            package_counts[package] += 1

    linked_advisories = set()
    linked_servers = set()
    for row in link_rows:
        row = row or {}
        advisory_id = _text(row.get("advisory_id"))
        server_id = _text(row.get("server_id"))
        if advisory_id:
            linked_advisories.add(advisory_id)
        if server_id:
            linked_servers.add(server_id)

    unlinked_advisories = sum(1 for key in advisory_keys if key not in linked_advisories)

    top_packages = [
        {"package": package, "count": count}
        for package, count in sorted(package_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ][:TOP_PACKAGE_LIMIT]

    return {
        "total_advisories": len(advisory_rows),
        "by_severity": dict(by_severity),
        "by_ecosystem": dict(by_ecosystem),
        "by_feed": dict(by_feed),
        "unlinked_advisories": unlinked_advisories,
        "linked_servers": len(linked_servers),
        "top_packages": top_packages,
    }


def _section(title: str, counts: Dict[str, int]) -> List[str]:
    lines = [title, "-" * len(title)]
    if not counts:
        lines.append("  (none)")
        return lines
    for key in sorted(counts, key=lambda name: (-counts[name], name)):
        lines.append(f"  {key}: {counts[key]}")
    return lines


def render_report(report: Dict[str, Any]) -> str:
    """Render the report dict as plain text."""
    report = report or {}
    by_severity = report.get("by_severity") or {}
    by_ecosystem = report.get("by_ecosystem") or {}
    by_feed = report.get("by_feed") or {}
    top_packages = report.get("top_packages") or []

    lines: List[str] = []
    lines.append("VULN ADVISORY SEVERITY / ECOSYSTEM REPORT")
    lines.append("=" * 41)
    lines.append(f"TOTAL ADVISORIES   : {report.get('total_advisories', 0)}")
    lines.append(f"LINKED SERVERS     : {report.get('linked_servers', 0)}")
    lines.append(f"UNLINKED ADVISORIES: {report.get('unlinked_advisories', 0)}")
    lines.append("")
    lines.extend(_section("SEVERITY BREAKDOWN", by_severity))
    lines.append("")
    lines.extend(_section("ECOSYSTEM BREAKDOWN", by_ecosystem))
    lines.append("")
    lines.extend(_section("FEED BREAKDOWN", by_feed))
    lines.append("")
    lines.append(f"TOP PACKAGES (limit {TOP_PACKAGE_LIMIT})")
    lines.append("-" * 28)
    if not top_packages:
        lines.append("  (none)")
    for entry in top_packages:
        lines.append(f"  {entry.get('package', '')}: {entry.get('count', 0)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Data layer (real app session over real app models)
# --------------------------------------------------------------------------

def load_rows(db: Session) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Read advisories + links from the app Postgres via the SQLAlchemy session."""
    advisory_rows = db.execute(
        select(
            VulnAdvisory.id,
            VulnAdvisory.feed,
            VulnAdvisory.severity,
            VulnAdvisory.ecosystem,
            VulnAdvisory.package,
            VulnAdvisory.published_at,
        )
    ).all()

    link_rows = db.execute(
        select(
            VulnLink.advisory_id,
            VulnLink.server_id,
            VulnLink.match_confidence,
        )
    ).all()

    advisories = [
        {
            "id": row.id,
            "feed": row.feed,
            "severity": row.severity,
            "ecosystem": row.ecosystem,
            "package": row.package,
            "published_at": row.published_at,
        }
        for row in advisory_rows
    ]
    links = [
        {
            "advisory_id": row.advisory_id,
            "server_id": row.server_id,
            "match_confidence": row.match_confidence,
        }
        for row in link_rows
    ]
    return advisories, links


def build_report_from_db(db: Session) -> Dict[str, Any]:
    advisories, links = load_rows(db)
    return build_report(advisories, links)


@router.get("/severity-ecology")
def severity_ecology_report(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Severity / ecosystem / feed breakdown of the advisory corpus."""
    return build_report_from_db(db)


def _open_session():
    """Open the repository's session dependency without assuming its decorator style."""
    supplied = get_session()
    if hasattr(supplied, "query"):
        return supplied, None
    iterator = iter(supplied)
    try:
        session = next(iterator)
    except StopIteration as exc:
        raise RuntimeError("get_session did not yield a database session") from exc
    return session, iterator


def _close_session(session, iterator) -> None:
    try:
        if session is not None and hasattr(session, "close"):
            session.close()
    finally:
        if iterator is not None and hasattr(iterator, "close"):
            iterator.close()


def run() -> int:
    """Build the report from the live app database and print it."""
    session, iterator = _open_session()
    try:
        report = build_report_from_db(session)
    finally:
        _close_session(session, iterator)
    print(render_report(report))
    return 0


def self_test() -> int:
    """Offline self-test: seeded rows through the real session dependency."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seed = TS()
    seed.add(VulnAdvisory(id="CVE-2026-0001", feed="ghsa", severity="CRITICAL",
                          ecosystem="npm", package="left-pad",
                          source_url="https://example.test/ghsa/1", published_at=now))
    seed.add(VulnAdvisory(id="CVE-2026-0002", feed="nvd", severity="CRITICAL",
                          ecosystem="pypi", package="requests",
                          source_url="https://example.test/nvd/2", published_at=now))
    seed.add(VulnAdvisory(id="CVE-2026-0003", feed="ghsa", severity="MEDIUM",
                          ecosystem="npm", package="left-pad",
                          source_url="https://example.test/ghsa/3", published_at=now))
    seed.add(VulnAdvisory(id="CVE-2026-0004", feed="nvd", severity="MEDIUM",
                          ecosystem="pypi", package="flask",
                          source_url="https://example.test/nvd/4", published_at=now))
    seed.add(VulnLink(advisory_id="CVE-2026-0001", server_id="srv-a",
                      match_basis="package_exact", match_value="left-pad",
                      match_confidence=1.0))
    seed.add(VulnLink(advisory_id="CVE-2026-0002", server_id="srv-b",
                      match_basis="package_exact", match_value="requests",
                      match_confidence=1.0))
    seed.commit()
    seed.close()

    test_app = FastAPI()
    test_app.include_router(router)

    def _override_session():
        session = TS()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = _override_session

    try:
        client = TestClient(test_app)
        response = client.get("/api/vuln-report/severity-ecology")
        assert response.status_code == 200, response.text
        report = response.json()

        assert report["total_advisories"] == 4, report
        assert report["by_severity"]["critical"] == 2, report
        assert report["by_severity"]["medium"] == 2, report
        assert report["by_ecosystem"]["npm"] == 2, report
        assert report["by_feed"]["ghsa"] == 2, report
        assert report["unlinked_advisories"] == 2, report
        assert report["linked_servers"] == 2, report
        assert report["top_packages"][0] == {"package": "left-pad", "count": 2}, report
        assert len(report["top_packages"]) == 3, report

        text = render_report(report)
        assert "SEVERITY" in text, text

        # pure-function contract, independent of the DB
        pure = build_report(
            [
                {"id": "A", "feed": "ghsa", "severity": "critical", "ecosystem": "npm", "package": "p"},
                {"id": "B", "feed": "nvd", "severity": "medium", "ecosystem": "pypi", "package": "p"},
                {"id": "C", "feed": "nvd", "severity": "critical", "ecosystem": "npm", "package": "q"},
                {"id": "D", "feed": "osv", "severity": "medium", "ecosystem": "pypi", "package": "q"},
            ],
            [
                {"advisory_id": "A", "server_id": "s1", "match_confidence": 1.0},
                {"advisory_id": "B", "server_id": "s2", "match_confidence": 1.0},
            ],
        )
        assert pure["total_advisories"] == 4, pure
        assert pure["by_severity"]["critical"] == 2, pure
        assert pure["unlinked_advisories"] == 2, pure
        assert "SEVERITY" in render_report(pure)

        # edge case: empty corpus
        empty = build_report([], [])
        assert empty["total_advisories"] == 0, empty
        assert empty["unlinked_advisories"] == 0, empty
        assert empty["linked_servers"] == 0, empty
        assert "SEVERITY" in render_report(empty)
    finally:
        test_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(self_test())
