"""vuln_advisory_coverage_report.py -- advisory-feed linkage coverage report.

Offline operator report (NO HTTP route): summarises how well the vuln advisory
feeds are actually linked into the MCP registry.

Standalone report generator:
    build_report(conn=None, days: int = 30) -> dict
    main(conn=None) -> int   (prints the report as JSON)

Data access is READ-ONLY through the app SQLAlchemy session (app.db.get_session +
app.models.VulnAdvisory / VulnLink / McpServerRegistry). No writes, no network,
no DELETE/DROP/TRUNCATE, no mcp_* mutations.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


def _now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cutoff(days: int) -> datetime:
    return _now_naive_utc() - timedelta(days=max(0, int(days)))


def _ratio(linked: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(float(linked) / float(total), 4)


def build_report(conn: Optional[Session] = None, days: int = 30) -> Dict[str, Any]:
    """Build the feed coverage report.

    conn: an open SQLAlchemy Session/Connection. When None a session is opened
    from app.db.get_session() (and closed again).
    days: window applied to vuln_advisories.published_at.
    """
    owns = conn is None
    db = conn
    if owns:
        db = next(get_session())
    try:
        cutoff = _cutoff(days)

        adv_rows = db.execute(
            select(VulnAdvisory.feed, VulnAdvisory.id).where(
                VulnAdvisory.published_at.isnot(None),
                VulnAdvisory.published_at >= cutoff,
            )
        ).all()

        feed_of: Dict[str, str] = {}
        per_feed_adv: Dict[str, int] = {}
        for feed, adv_id in adv_rows:
            f = feed or "unknown"
            feed_of[adv_id] = f
            per_feed_adv[f] = per_feed_adv.get(f, 0) + 1

        link_rows: List[Any] = []
        if feed_of:
            link_rows = db.execute(
                select(VulnLink.advisory_id, VulnLink.match_confidence)
                .join(McpServerRegistry,
                      McpServerRegistry.server_id == VulnLink.server_id)
                .where(VulnLink.advisory_id.in_(list(feed_of.keys())))
            ).all()

        linked_adv: Dict[str, int] = {}
        conf_sum: Dict[str, float] = {}
        conf_n: Dict[str, int] = {}
        seen_adv: set = set()
        for adv_id, conf in link_rows:
            f = feed_of.get(adv_id)
            if f is None:
                continue
            seen_adv.add(adv_id)
            try:
                c = float(conf if conf is not None else 0.0)
            except (TypeError, ValueError):
                c = 0.0
            conf_sum[f] = conf_sum.get(f, 0.0) + c
            conf_n[f] = conf_n.get(f, 0) + 1
        for adv_id in seen_adv:
            f = feed_of[adv_id]
            linked_adv[f] = linked_adv.get(f, 0) + 1

        feeds: List[Dict[str, Any]] = []
        for f in sorted(per_feed_adv):
            total = per_feed_adv[f]
            linked = linked_adv.get(f, 0)
            n = conf_n.get(f, 0)
            feeds.append({
                "feed": f,
                "advisories": total,
                "linked": linked,
                "coverage_ratio": _ratio(linked, total),
                "avg_confidence": (round(conf_sum.get(f, 0.0) / n, 4) if n else None),
            })

        total_adv = sum(per_feed_adv.values())
        total_linked = len(seen_adv)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "days": int(days),
            "feeds": feeds,
            "totals": {
                "advisories": total_adv,
                "linked": total_linked,
                "coverage_ratio": _ratio(total_linked, total_adv),
            },
        }
    finally:
        if owns:
            try:
                db.close()
            except Exception:
                pass


def main(conn: Optional[Session] = None) -> int:
    """Run the report against the live app DB (read-only) and print it as JSON."""
    try:
        report = build_report(conn)
    except Exception as exc:
        print(f"vuln advisory coverage report unavailable: {exc}")
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _self_test() -> None:
    from datetime import datetime as _dt

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    now = _dt.now(timezone.utc).replace(tzinfo=None)
    s = TS()
    try:
        s.add(McpServerRegistry(server_id="srv1", name="One",
                                registry_source="alpha"))
        s.add(McpServerRegistry(server_id="srv2", name="Two",
                                registry_source="beta"))
        s.add(VulnAdvisory(id="ADV-001", feed="ghsa", severity="HIGH",
                            ecosystem="npm", source_url="https://example.invalid/a1",
                            published_at=now))
        s.add(VulnAdvisory(id="ADV-002", feed="ghsa", severity="CRITICAL",
                            ecosystem="pypi", source_url="https://example.invalid/a2",
                            published_at=now))
        s.add(VulnAdvisory(id="ADV-003", feed="osv", severity="MEDIUM",
                            ecosystem="npm", source_url="https://example.invalid/a3",
                            published_at=now))
        s.add(VulnLink(id=1, advisory_id="ADV-001", server_id="srv1",
                       match_basis="package_exact", match_value="pkg-one",
                       match_confidence=0.9, linked_at=now))
        s.add(VulnLink(id=2, advisory_id="ADV-002", server_id="srv2",
                       match_basis="repo_exact", match_value="https://example.invalid/two",
                       match_confidence=0.7, linked_at=now))
        s.commit()
    finally:
        s.close()

    report = build_report(s, days=30)
    assert len(report["feeds"]) == 2, report
    assert report["totals"]["advisories"] == 3, report
    assert report["totals"]["linked"] == 2, report
    assert 0.0 <= report["totals"]["coverage_ratio"] <= 1.0, report
    assert report["days"] == 30, report
    _dt.fromisoformat(report["generated_at"])
    by_feed = {f["feed"]: f for f in report["feeds"]}
    assert by_feed["ghsa"]["advisories"] == 2, report
    assert by_feed["ghsa"]["linked"] == 2, report
    assert by_feed["osv"]["advisories"] == 1, report
    assert by_feed["osv"]["linked"] == 0, report
    assert by_feed["osv"]["avg_confidence"] is None, report
    assert abs(by_feed["ghsa"]["avg_confidence"] - 0.8) < 1e-6, report
    json.dumps(report)

    # edge case: a narrow window excludes every advisory
    empty = build_report(s, days=0)
    assert empty["totals"]["advisories"] == 0, empty
    assert empty["totals"]["coverage_ratio"] == 0.0, empty

    # the JSON path of main() works against the seeded session
    assert main(s) == 0
    s.close()
    eng.dispose()


if __name__ == "__main__":
    try:
        _self_test()
    except Exception as exc:  # noqa: BLE001 - CI self-test reports, never raises
        print(f"FAIL: {exc!r}")
        raise SystemExit(1)
    print("PASS")
