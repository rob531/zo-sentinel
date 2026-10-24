"""vuln_link_confidence_report.py -- how confidently are advisories linked to MCP servers?

Reads the deterministic linkage table (vuln_links) plus the advisory catalogue
(vuln_advisories) through an INJECTED SQLAlchemy session and summarises the
confidence distribution of the linkages.

REPORT ONLY: it never writes to the database, never opens a connection of its
own and never touches the network. The session/session-factory is supplied by
the caller (app.db.SessionLocal in the app tier, an in-memory SQLite session in
the self-test).

Data layer: the REAL app models (app.models.VulnLink / app.models.VulnAdvisory)
and the canonical app.db seam (app.db.get_session) -- no inline models, no
re-declared Base, no local engine.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

# Confidence buckets: < LOW_CONFIDENCE_MAX -> low, < MEDIUM_CONFIDENCE_MAX -> medium, else high.
LOW_CONFIDENCE_MAX = 0.5
MEDIUM_CONFIDENCE_MAX = 0.8
TOP_ADVISORIES_LIMIT = 10

REPORT_FILENAME = "vuln_link_confidence_report.json"

router = APIRouter(prefix="/api", tags=["vuln-links"])


def _bucket(confidence: float) -> str:
    if confidence < LOW_CONFIDENCE_MAX:
        return "low"
    if confidence < MEDIUM_CONFIDENCE_MAX:
        return "medium"
    return "high"


def _confidence(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _build_report(session: Session) -> Dict[str, Any]:
    """Report core: one injected session, no connection of our own."""
    links = session.execute(
        select(
            VulnLink.id,
            VulnLink.advisory_id,
            VulnLink.server_id,
            VulnLink.match_basis,
            VulnLink.match_value,
            VulnLink.match_confidence,
        )
    ).all()

    advisories = session.execute(
        select(
            VulnAdvisory.id,
            VulnAdvisory.summary,
            VulnAdvisory.severity,
            VulnAdvisory.published_at,
            VulnAdvisory.fetched_at,
        )
    ).all()

    advisory_by_id = {row.id: row for row in advisories}

    by_match_basis: Dict[str, int] = {}
    buckets = {"low": 0, "medium": 0, "high": 0}
    per_advisory: Dict[str, Dict[str, Any]] = {}
    linked_advisory_ids = set()

    for row in links:
        basis = row.match_basis or "unknown"
        by_match_basis[basis] = by_match_basis.get(basis, 0) + 1

        confidence = _confidence(row.match_confidence)
        buckets[_bucket(confidence)] += 1

        linked_advisory_ids.add(row.advisory_id)
        entry = per_advisory.setdefault(
            row.advisory_id,
            {"link_count": 0, "confidences": [], "server_ids": set()},
        )
        entry["link_count"] += 1
        entry["confidences"].append(confidence)
        entry["server_ids"].add(row.server_id)

    top_advisories: List[Dict[str, Any]] = []
    for advisory_id, entry in per_advisory.items():
        adv = advisory_by_id.get(advisory_id)
        confidences = entry["confidences"]
        top_advisories.append({
            "advisory_id": advisory_id,
            "summary": getattr(adv, "summary", None),
            "severity": getattr(adv, "severity", None),
            "link_count": entry["link_count"],
            "avg_match_confidence": (
                round(sum(confidences) / len(confidences), 4) if confidences else 0.0
            ),
        })
    top_advisories.sort(
        key=lambda a: (-a["link_count"], -a["avg_match_confidence"], a["advisory_id"])
    )
    top_advisories = top_advisories[:TOP_ADVISORIES_LIMIT]

    unlinked = sum(1 for adv in advisories if adv.id not in linked_advisory_ids)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_links": len(links),
        "by_match_basis": by_match_basis,
        "confidence_buckets": buckets,
        "top_advisories": top_advisories,
        "unlinked_advisories": unlinked,
    }


def build_report(db_session_factory) -> Dict[str, Any]:
    """Build the link-confidence report.

    db_session_factory: a zero-arg callable returning a SQLAlchemy Session
    (e.g. app.db.SessionLocal). The session is closed here if it exposes
    close().
    """
    session = db_session_factory()
    try:
        return _build_report(session)
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


@router.get("/vuln-links/confidence-report")
def vuln_link_confidence_report(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """HTTP surface over the same report, using the canonical app.db session."""
    return _build_report(db)


def write_report(report: Dict[str, Any], out_dir: str) -> str:
    """Write the report as JSON under out_dir; returns the file path."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, REPORT_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    return path


if __name__ == "__main__":  # CI-safe self-test: real models, SQLite session override
    import tempfile

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    VulnAdvisory.__table__.create(eng)
    VulnLink.__table__.create(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    seed = TS()
    seed.add_all([
        VulnAdvisory(id="GHSA-0001", feed="ghsa", summary="RCE in demo pkg",
                     severity="CRITICAL", source_url="https://github.com/advisories/GHSA-0001",
                     published_at=now, fetched_at=now),
        VulnAdvisory(id="GHSA-0002", feed="ghsa", summary="DoS in other pkg",
                     severity="MEDIUM", source_url="https://github.com/advisories/GHSA-0002",
                     published_at=now, fetched_at=now),
        VulnAdvisory(id="GHSA-0003", feed="ghsa", summary="Never linked advisory",
                     severity="LOW", source_url="https://github.com/advisories/GHSA-0003",
                     published_at=now, fetched_at=now),
    ])
    seed.add_all([
        VulnLink(advisory_id="GHSA-0001", server_id="srv-a", match_basis="repo_exact",
                 match_value="repo:github.com/o/a", match_confidence=0.2, linked_at=now),
        VulnLink(advisory_id="GHSA-0002", server_id="srv-b", match_basis="package_exact",
                 match_value="pkg:npm/demo", match_confidence=0.6, linked_at=now),
        VulnLink(advisory_id="GHSA-0001", server_id="srv-c", match_basis="package_exact",
                 match_value="pkg:npm/demo2", match_confidence=0.95, linked_at=now),
    ])
    seed.commit()
    seed.close()

    report = build_report(TS)
    assert report["total_links"] == 3, report
    assert report["confidence_buckets"] == {"low": 1, "medium": 1, "high": 1}, report
    assert report["by_match_basis"] == {"repo_exact": 1, "package_exact": 2}, report
    assert report["top_advisories"], report
    assert report["unlinked_advisories"] == 1, report   # GHSA-0003 has no links
    top = report["top_advisories"][0]
    assert top["advisory_id"] == "GHSA-0001", top
    assert top["link_count"] == 2, top
    assert abs(top["avg_match_confidence"] - 0.575) < 1e-6, top
    assert top["severity"] == "CRITICAL", top

    # edge case: empty database -> zeroed report, no crash
    empty_eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
    VulnAdvisory.__table__.create(empty_eng)
    VulnLink.__table__.create(empty_eng)
    empty_report = build_report(sessionmaker(bind=empty_eng))
    assert empty_report["total_links"] == 0, empty_report
    assert empty_report["confidence_buckets"] == {"low": 0, "medium": 0, "high": 0}
    assert empty_report["unlinked_advisories"] == 0, empty_report
    assert empty_report["top_advisories"] == [], empty_report

    with tempfile.TemporaryDirectory() as tmp:
        path = write_report(report, tmp)
        assert os.path.basename(path) == REPORT_FILENAME, path
        with open(path, encoding="utf-8") as fh:
            written = json.load(fh)
        assert written["total_links"] == 3, written

    print("PASS")
