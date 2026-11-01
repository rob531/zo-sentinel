"""advisory_link_age_ledger_report.py -- ledger of advisory->server link ages.

Pure part (stdlib only): build_link_age_ledger(advisory_rows, link_rows, now)
returns a dict describing how old every advisory->server link is and which
links drift toward staleness.

Impure part: fetch_advisory_rows(db) / fetch_link_rows(db) read the REAL app
data layer (`vuln_advisories` / `vuln_links` through the app.db SQLAlchemy
session) -- read-only, no writes, no network, no duckdb, no CSV/JSON inputs.
"""
from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter(prefix="/api/reports", tags=["reports"])

AGE_BUCKET_NAMES = ("0_7d", "8_30d", "31_90d", "91_plus")
LOW_CONFIDENCE_THRESHOLD = 0.5
DRIFT_AGE_DAYS = 31
STALE_AGE_DAYS = 91


# ---------------------------------------------------------------- helpers


def _as_utc(value: Any) -> Optional[datetime]:
    """Parse a timestamp into an aware UTC datetime (None when unparsable)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    dt = _as_utc(value)
    return dt.isoformat() if dt else None


def _age_days(reference: datetime, value: Any) -> Optional[int]:
    """Whole days between `reference` and `value` (None when unparsable)."""
    ts = _as_utc(value)
    if ts is None:
        return None
    seconds = (reference - ts).total_seconds()
    return max(0, int(seconds // 86400))


def _bucket_for(age_days: int) -> str:
    if age_days <= 7:
        return "0_7d"
    if age_days <= 30:
        return "8_30d"
    if age_days <= 90:
        return "31_90d"
    return "91_plus"


def _confidence(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- pure core


def build_link_age_ledger(advisory_rows: List[Dict[str, Any]],
                          link_rows: List[Dict[str, Any]],
                          now: datetime) -> Dict[str, Any]:
    """Ledger of advisory->server link ages.

    `advisory_rows`: dicts with id, feed, severity, package, published_at,
    fetched_at. `link_rows`: dicts with advisory_id, server_id, linked_at,
    match_confidence. `now` is the aware reference time.

    The age buckets count TRUSTED links only -- a link whose advisory is
    unknown (orphan) or whose match confidence is below the threshold is
    reported separately instead of skewing the age histogram. Links whose
    timestamp cannot be parsed are counted in `unknown_age_links` instead of
    raising. The input lists are never mutated.
    """
    reference = _as_utc(now) or datetime.now(timezone.utc)
    advisories = [dict(a) for a in (advisory_rows or [])]
    links = [dict(link) for link in (link_rows or [])]

    advisory_index: Dict[Any, Dict[str, Any]] = {}
    for advisory in advisories:
        key = advisory.get("id")
        if key is not None and key not in advisory_index:
            advisory_index[key] = advisory

    buckets: Dict[str, int] = {name: 0 for name in AGE_BUCKET_NAMES}
    unknown_age_links = 0
    low_confidence_links = 0
    orphan_links: List[Any] = []
    per_advisory: Dict[Any, List[Dict[str, Any]]] = {}

    for link in links:
        advisory_id = link.get("advisory_id")
        server_id = link.get("server_id")
        confidence = _confidence(link.get("match_confidence"))
        age_days = _age_days(reference, link.get("linked_at"))

        if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
            low_confidence_links += 1

        is_orphan = advisory_id not in advisory_index
        if is_orphan and server_id not in orphan_links:
            orphan_links.append(server_id)

        if age_days is None:
            unknown_age_links += 1

        trusted = (not is_orphan) and (confidence is None
                                       or confidence >= LOW_CONFIDENCE_THRESHOLD)
        if trusted and age_days is not None:
            buckets[_bucket_for(age_days)] += 1

        if not is_orphan:
            per_advisory.setdefault(advisory_id, []).append({
                "server_id": server_id,
                "age_days": age_days,
                "match_confidence": confidence,
            })

    rows: List[Dict[str, Any]] = []
    for advisory in advisories:
        advisory_id = advisory.get("id")
        items = per_advisory.get(advisory_id, [])
        ages = [i["age_days"] for i in items if i["age_days"] is not None]
        drifting = sum(1 for a in ages if a >= DRIFT_AGE_DAYS)
        stale = sum(1 for a in ages if a >= STALE_AGE_DAYS)
        if not items:
            state = "no_links"
        elif stale:
            state = "stale"
        elif drifting:
            state = "drifting"
        else:
            state = "fresh"
        rows.append({
            "advisory_id": advisory_id,
            "feed": advisory.get("feed"),
            "severity": advisory.get("severity"),
            "package": advisory.get("package"),
            "published_at": _iso(advisory.get("published_at")),
            "fetched_at": _iso(advisory.get("fetched_at")),
            "link_count": len(items),
            "oldest_link_age_days": max(ages) if ages else None,
            "drifting_links": drifting,
            "stale_links": stale,
            "state": state,
            "servers": [i["server_id"] for i in items],
        })

    return {
        "generated_at": reference.isoformat(),
        "total_advisories": len(advisories),
        "total_links": len(links),
        "age_buckets": buckets,
        "orphan_links": orphan_links,
        "low_confidence_links": low_confidence_links,
        "unknown_age_links": unknown_age_links,
        "rows": rows,
    }


# ---------------------------------------------------------------- data access


def fetch_advisory_rows(db: Session) -> List[Dict[str, Any]]:
    """Read the advisory side of the ledger from the authoritative APP table."""
    stmt = select(
        VulnAdvisory.id,
        VulnAdvisory.feed,
        VulnAdvisory.severity,
        VulnAdvisory.package,
        VulnAdvisory.published_at,
        VulnAdvisory.fetched_at,
    )
    return [
        {
            "id": r[0],
            "feed": r[1],
            "severity": r[2],
            "package": r[3],
            "published_at": r[4],
            "fetched_at": r[5],
        }
        for r in db.execute(stmt).all()
    ]


def fetch_link_rows(db: Session) -> List[Dict[str, Any]]:
    """Read the link side of the ledger from the authoritative APP table."""
    stmt = select(
        VulnLink.advisory_id,
        VulnLink.server_id,
        VulnLink.linked_at,
        VulnLink.match_confidence,
    )
    return [
        {
            "advisory_id": r[0],
            "server_id": r[1],
            "linked_at": r[2],
            "match_confidence": r[3],
        }
        for r in db.execute(stmt).all()
    ]


@contextmanager
def _session_scope():
    """One real app session (read-only use), closed on exit."""
    provider = get_session()
    session = next(provider)
    try:
        yield session
    finally:
        session.close()


def build_report(now: Optional[datetime] = None, db: Optional[Session] = None) -> Dict[str, Any]:
    """Build the ledger from the real app tables (read-only)."""
    reference = now or datetime.now(timezone.utc)
    if db is not None:
        return build_link_age_ledger(fetch_advisory_rows(db), fetch_link_rows(db), reference)
    with _session_scope() as session:
        return build_link_age_ledger(fetch_advisory_rows(session),
                                     fetch_link_rows(session), reference)


@router.get("/advisory-link-age")
def advisory_link_age_ledger(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Link-age ledger for every advisory->server link."""
    return build_report(db=db)


# ---------------------------------------------------------------- entrypoint


def run() -> Dict[str, Any]:
    """Build the ledger from the real data layer and print it as JSON."""
    report = build_report()
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Advisory link-age ledger (JSON)")
    parser.parse_args(argv)
    run()
    return 0


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite session override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)

    # --- fixture 1: three trusted links aged 3 / 40 / 200 days ---
    advisories = [
        {"id": "CVE-2026-0001", "feed": "osv", "severity": "HIGH",
         "package": "left-pad", "published_at": "2026-10-01T00:00:00+00:00",
         "fetched_at": "2026-10-01T01:00:00+00:00"},
        {"id": "CVE-2026-0002", "feed": "ghsa", "severity": "MEDIUM",
         "package": "right-pad", "published_at": "2026-09-01T00:00:00+00:00",
         "fetched_at": "2026-09-01T01:00:00+00:00"},
        {"id": "CVE-2026-0003", "feed": "nvd", "severity": "CRITICAL",
         "package": "center-pad", "published_at": "2025-01-01T00:00:00+00:00",
         "fetched_at": "2025-01-01T01:00:00+00:00"},
    ]
    links = [
        {"advisory_id": "CVE-2026-0001", "server_id": "srv_fresh",
         "linked_at": "2026-10-27T12:00:00+00:00", "match_confidence": 1.0},
        {"advisory_id": "CVE-2026-0002", "server_id": "srv_mid",
         "linked_at": "2026-09-20T12:00:00+00:00", "match_confidence": 1.0},
        {"advisory_id": "CVE-2026-0003", "server_id": "srv_old",
         "linked_at": "2026-04-13T12:00:00+00:00", "match_confidence": 1.0},
    ]
    report = build_link_age_ledger(advisories, links, NOW)
    assert report["total_advisories"] == 3, report
    assert report["total_links"] == 3, report
    assert report["age_buckets"]["0_7d"] == 1, report
    assert report["age_buckets"]["8_30d"] == 0, report
    assert report["age_buckets"]["31_90d"] == 1, report
    assert report["age_buckets"]["91_plus"] == 1, report
    assert sum(report["age_buckets"].values()) == 3, report
    assert report["orphan_links"] == [], report
    assert report["low_confidence_links"] == 0, report
    assert len(report["rows"]) == 3, report
    assert report["rows"][0]["state"] == "fresh", report
    assert report["rows"][2]["state"] == "stale", report

    # --- fixture 2: one orphan link + one low-confidence link ---
    orphan_links = [{"advisory_id": "CVE-9999-9999", "server_id": "srv_orphan",
                     "linked_at": "2026-10-01T00:00:00+00:00", "match_confidence": 1.0}]
    low_conf = [{"advisory_id": "CVE-2026-0001", "server_id": "srv_lowconf",
                 "linked_at": "2026-10-29T00:00:00+00:00", "match_confidence": 0.2}]
    mixed = build_link_age_ledger(advisories, links + orphan_links + low_conf, NOW)
    assert sum(mixed["age_buckets"].values()) == 3, mixed
    assert "srv_orphan" in mixed["orphan_links"], mixed
    assert mixed["low_confidence_links"] == 1, mixed
    assert mixed["total_links"] == 5, mixed

    # --- fixture 3: unparsable timestamp -> unknown bucket, no raise ---
    bad = [{"advisory_id": "CVE-2026-0001", "server_id": "srv_bad",
            "linked_at": "not-a-timestamp", "match_confidence": 1.0}]
    broken = build_link_age_ledger(advisories, bad, NOW)
    assert broken["unknown_age_links"] == 1, broken
    assert sum(broken["age_buckets"].values()) == 0, broken

    # --- inputs are never mutated ---
    before_a = [dict(a) for a in advisories]
    before_l = [dict(link) for link in links]
    build_link_age_ledger(advisories, links, NOW)
    assert advisories == before_a, advisories
    assert links == before_l, links

    # --- empty input ---
    empty = build_link_age_ledger([], [], NOW)
    assert empty["total_links"] == 0 and empty["total_advisories"] == 0, empty
    assert sum(empty["age_buckets"].values()) == 0, empty

    # --- real data layer path (SQLite session over the real models) ---
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(VulnAdvisory(id="CVE-2026-0001", feed="osv", severity="HIGH",
                       package="left-pad", source_url="https://osv.dev/CVE-2026-0001",
                       published_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                       fetched_at=datetime(2026, 10, 1, 1, tzinfo=timezone.utc)))
    s.add(VulnAdvisory(id="CVE-2026-0002", feed="ghsa", severity="LOW",
                       package="right-pad", source_url="https://github.com/advisories/0002",
                       published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                       fetched_at=datetime(2026, 9, 1, 1, tzinfo=timezone.utc)))
    s.add(VulnLink(id=1, advisory_id="CVE-2026-0001", server_id="srv_fresh",
                   match_basis="package_exact", match_value="left-pad",
                   match_confidence=1.0,
                   linked_at=datetime(2026, 10, 27, 12, tzinfo=timezone.utc)))
    s.add(VulnLink(id=2, advisory_id="CVE-2026-0002", server_id="srv_mid",
                   match_basis="package_exact", match_value="right-pad",
                   match_confidence=0.9,
                   linked_at=datetime(2026, 9, 20, 12, tzinfo=timezone.utc)))
    s.add(VulnLink(id=3, advisory_id="CVE-4040-4040", server_id="srv_orphan",
                   match_basis="package_exact", match_value="ghost-pad",
                   match_confidence=1.0,
                   linked_at=datetime(2026, 10, 1, tzinfo=timezone.utc)))
    s.commit()

    live = build_report(now=NOW, db=s)
    assert live["total_advisories"] == 2, live
    assert live["total_links"] == 3, live
    assert live["age_buckets"]["0_7d"] == 1, live
    assert live["age_buckets"]["31_90d"] == 1, live
    assert "srv_orphan" in live["orphan_links"], live
    assert len(live["rows"]) == 2, live

    # --- endpoint over the REAL data layer (SQLite override of get_session) ---
    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    r = c.get("/api/reports/advisory-link-age")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_links"] == 3, body
    assert "srv_orphan" in body["orphan_links"], body
    s.close()

    print("PASS")
