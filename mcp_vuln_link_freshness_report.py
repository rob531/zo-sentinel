"""mcp_vuln_link_freshness_report.py -- READ-ONLY freshness/confidence report over
the server->advisory vulnerability linkage surface.

Reads the REAL app tables (vuln_links, vuln_advisories) through the app.db
SQLAlchemy session -- no inline models, no stub DB. No writes, no network.

build_report(session=None) -> dict
main() prints the report as JSON.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_session  # noqa: F401  (get_session: data-layer seam)
from app.models import VulnAdvisory, VulnLink

STALE_AFTER_DAYS = 30
MAX_STALE_ROWS = 50

CONF_LOW = 0.3
CONF_MID = 0.6

AGE_BUCKET_KEYS = ("lt_1d", "1d_to_7d", "7d_to_30d", "gt_30d")
CONF_BUCKET_KEYS = ("lt_0_3", "0_3_to_0_6", "gt_0_6")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalise a DB datetime (naive or tz-aware) to naive UTC."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _confidence_bucket(conf: Optional[float]) -> str:
    if conf is None:
        return CONF_BUCKET_KEYS[0]
    if conf < CONF_LOW:
        return CONF_BUCKET_KEYS[0]
    if conf <= CONF_MID:
        return CONF_BUCKET_KEYS[1]
    return CONF_BUCKET_KEYS[2]


def _age_bucket(age_hours: Optional[float]) -> str:
    if age_hours is None:
        return AGE_BUCKET_KEYS[0]
    if age_hours < 24.0:
        return AGE_BUCKET_KEYS[0]
    if age_hours < 24.0 * 7:
        return AGE_BUCKET_KEYS[1]
    if age_hours < 24.0 * 30:
        return AGE_BUCKET_KEYS[2]
    return AGE_BUCKET_KEYS[3]


def _iso(dt: Optional[datetime]) -> Optional[str]:
    dt = _naive_utc(dt)
    return dt.isoformat() if dt is not None else None


def _build_report(session: Session) -> Dict[str, Any]:
    now = _now_utc()
    try:
        links = list(session.execute(select(VulnLink)).scalars().all())
        advisories = list(session.execute(select(VulnAdvisory)).scalars().all())
    except Exception as exc:  # missing table / unreachable DB -> report the error, never raise
        return {"error": str(exc), "stale_links": []}

    links_by_match_basis: Dict[str, int] = {}
    links_by_confidence_bucket: Dict[str, int] = {k: 0 for k in CONF_BUCKET_KEYS}
    age_buckets: Dict[str, int] = {k: 0 for k in AGE_BUCKET_KEYS}
    ages: List[float] = []
    stale_links: List[Dict[str, Any]] = []
    stale_cutoff = now - timedelta(days=STALE_AFTER_DAYS)

    for link in links:
        basis = link.match_basis or "unknown"
        links_by_match_basis[basis] = links_by_match_basis.get(basis, 0) + 1
        links_by_confidence_bucket[_confidence_bucket(link.match_confidence)] += 1

        linked_at = _naive_utc(link.linked_at)
        if linked_at is None:
            continue
        age_hours = max(0.0, (now - linked_at).total_seconds() / 3600.0)
        ages.append(age_hours)
        age_buckets[_age_bucket(age_hours)] += 1
        if linked_at <= stale_cutoff and len(stale_links) < MAX_STALE_ROWS:
            stale_links.append({
                "server_id": link.server_id,
                "advisory_id": link.advisory_id,
                "linked_at": _iso(linked_at),
            })

    never_fetched = sum(1 for a in advisories if a.fetched_at is None)

    return {
        "generated_at": now.replace(tzinfo=timezone.utc).isoformat(),
        "total_links": len(links),
        "links_by_match_basis": links_by_match_basis,
        "links_by_confidence_bucket": links_by_confidence_bucket,
        "age_buckets": age_buckets,
        "advisories_total": len(advisories),
        "advisories_never_fetched": never_fetched,
        "median_link_age_hours": (round(float(statistics.median(ages)), 3) if ages else None),
        "stale_links": stale_links,
    }


def build_report(session: Optional[Session] = None) -> Dict[str, Any]:
    """Freshness + confidence report for the server->advisory link surface."""
    if session is not None:
        return _build_report(session)
    s = SessionLocal()
    try:
        return _build_report(s)
    finally:
        s.close()


def main() -> None:
    report = build_report()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite session override
    from sqlalchemy import (Column, DateTime, Float, Integer, JSON, MetaData, String,
                            Table, Text, UniqueConstraint, create_engine)
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    # Local DDL for the two tables so fetched_at may be NULL (provenance gap probe).
    md = MetaData()
    Table("vuln_advisories", md,
          Column("id", String(64), primary_key=True),
          Column("feed", String(16), nullable=False),
          Column("summary", Text),
          Column("severity", String(16)),
          Column("ecosystem", String(32)),
          Column("package", String(256)),
          Column("affected_ranges", JSON),
          Column("aliases", JSON),
          Column("source_url", Text, nullable=False),
          Column("published_at", DateTime),
          Column("fetched_at", DateTime, nullable=True),
          Column("identities", JSON),
          Column("content_hash", String(32)))
    Table("vuln_links", md,
          Column("id", Integer, primary_key=True, autoincrement=True),
          Column("advisory_id", String(64), nullable=False),
          Column("server_id", String(128), nullable=False),
          Column("match_basis", String(32), nullable=False),
          Column("match_value", String(256), nullable=False),
          Column("match_confidence", Float, nullable=False),
          Column("linked_at", DateTime),
          UniqueConstraint("advisory_id", "server_id", name="uq_advisory_server"))
    md.create_all(eng)
    Base.metadata.create_all(eng)

    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    now = _now_utc()
    s.add(VulnAdvisory(id="CVE-2025-0001", feed="osv", severity="HIGH",
                        ecosystem="npm", package="left-pad",
                        source_url="https://osv.dev/vulnerability/CVE-2025-0001",
                        published_at=now - timedelta(days=90),
                        fetched_at=now - timedelta(days=89)))
    s.add(VulnAdvisory(id="CVE-2025-0002", feed="ghsa", severity="CRITICAL",
                        ecosystem="pypi", package="badpkg",
                        source_url="https://github.com/advisories/GHSA-0002",
                        published_at=now - timedelta(days=10),
                        fetched_at=None))
    for _i, (_adv, _srv, _basis, _conf, _age) in enumerate((
            ("CVE-2025-0001", "srv1", "repo_exact", 0.5, timedelta(hours=1)),
            ("CVE-2025-0001", "srv2", "package_exact", 0.4, timedelta(days=3)),
            ("CVE-2025-0002", "srv3", "repo_exact", 0.2, timedelta(days=60)),
            ("CVE-2025-0002", "srv4", "package_exact", 0.9, timedelta(days=2)),
    ), start=1):
        s.add(VulnLink(id=_i, advisory_id=_adv, server_id=_srv, match_basis=_basis,
                       match_value="pkg", match_confidence=_conf,
                       linked_at=now - _age))
    s.commit()

    rep = build_report(s)
    assert rep["total_links"] == 4, rep
    assert rep["advisories_total"] == 2, rep
    assert rep["advisories_never_fetched"] == 1, rep
    assert rep["age_buckets"]["gt_30d"] == 1, rep
    assert rep["links_by_confidence_bucket"]["gt_0_6"] == 1, rep
    assert rep["median_link_age_hours"] is not None, rep
    assert rep["age_buckets"]["lt_1d"] == 1, rep
    assert rep["age_buckets"]["1d_to_7d"] == 2, rep
    assert len(rep["stale_links"]) == 1, rep
    assert rep["links_by_match_basis"]["repo_exact"] == 2, rep

    # missing table -> error dict, never an exception
    eng2 = create_engine("sqlite://", connect_args={"check_same_thread": False},
                         poolclass=StaticPool)
    TS2 = sessionmaker(bind=eng2, autoflush=False, autocommit=False)
    s2 = TS2()
    rep2 = build_report(s2)
    assert "error" in rep2 and rep2["stale_links"] == [], rep2
    s2.close()
    s.close()
    print("PASS")
