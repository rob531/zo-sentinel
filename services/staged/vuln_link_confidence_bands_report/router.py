# deps: fastapi, sqlalchemy
"""vuln_link_confidence_bands_report.py -- profiles the quality of CVE->MCP
vulnerability links by match-confidence band, so analysts can see whether links
are being asserted from strong or weak evidence.

Pure layer (stdlib only): build_report(rows) -> dict and write_report(rows, path)
-> str. Rows are dicts shaped from the vuln_links table:
    {server_id, advisory_id, match_basis, match_value, match_confidence}
The pure layer touches no network, no database, no protected modules.

Data seam: fetch_link_rows(db) / build_report_from_db(db) read the SAME
authoritative APP table through the REAL app data layer (from app.db import
get_session / from app.models import VulnLink), read-only, no writes.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from statistics import fmean
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink

router = APIRouter(prefix="/api/reports", tags=["reports"])

STRONG_MIN = 0.8
MEDIUM_MIN = 0.5
BAND_NAMES = ("strong", "medium", "weak")
UNKNOWN_BASIS = "unknown"


# ---------------------------------------------------------------- helpers


def _confidence(value: Any) -> float:
    """Coerce a match_confidence value into a float (unusable -> 0.0)."""
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _band_for(confidence: float) -> str:
    """Fixed bands: strong (>=0.8), medium (0.5-0.79), weak (<0.5)."""
    if confidence >= STRONG_MIN:
        return "strong"
    if confidence >= MEDIUM_MIN:
        return "medium"
    return "weak"


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


# ---------------------------------------------------------------- pure core


def build_report(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Profile link quality by confidence band.

    `rows`: dicts with server_id, advisory_id, match_basis, match_value,
    match_confidence. The input list is never mutated.

    Returns {"total": int,
             "bands": [{"band", "count", "share", "distinct_servers",
                        "distinct_advisories"}],
             "basis_breakdown": [{"match_basis", "count", "avg_confidence"}],
             "weak_link_examples": [{"server_id", "advisory_id", "match_basis",
                                     "match_value", "match_confidence"}]}.
    Shares are fractions rounded to 4dp and sum to 1.0 across the populated
    bands.
    """
    band_rows: Dict[str, List[Dict[str, Any]]] = {name: [] for name in BAND_NAMES}
    band_servers: Dict[str, set] = {name: set() for name in BAND_NAMES}
    band_advisories: Dict[str, set] = {name: set() for name in BAND_NAMES}
    basis_counts: Counter = Counter()
    basis_confidences: Dict[str, List[float]] = defaultdict(list)
    total = 0

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        total += 1
        confidence = _confidence(row.get("match_confidence"))
        band = _band_for(confidence)
        band_rows[band].append(row)
        server_id = _text(row.get("server_id"))
        advisory_id = _text(row.get("advisory_id"))
        if server_id:
            band_servers[band].add(server_id)
        if advisory_id:
            band_advisories[band].add(advisory_id)
        basis = _text(row.get("match_basis")) or UNKNOWN_BASIS
        basis_counts[basis] += 1
        basis_confidences[basis].append(confidence)

    shares = _band_shares({name: len(band_rows[name]) for name in BAND_NAMES}, total)

    bands: List[Dict[str, Any]] = []
    for name in BAND_NAMES:
        bands.append({
            "band": name,
            "count": len(band_rows[name]),
            "share": shares[name],
            "distinct_servers": len(band_servers[name]),
            "distinct_advisories": len(band_advisories[name]),
        })

    basis_breakdown: List[Dict[str, Any]] = []
    for basis, count in sorted(basis_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        values = basis_confidences[basis]
        basis_breakdown.append({
            "match_basis": basis,
            "count": count,
            "avg_confidence": round(fmean(values), 4) if values else 0.0,
        })

    weak_link_examples: List[Dict[str, Any]] = []
    for row in sorted(band_rows["weak"],
                      key=lambda r: (_confidence(r.get("match_confidence")),
                                     _text(r.get("server_id")),
                                     _text(r.get("advisory_id")))):
        weak_link_examples.append({
            "server_id": _text(row.get("server_id")),
            "advisory_id": _text(row.get("advisory_id")),
            "match_basis": _text(row.get("match_basis")) or UNKNOWN_BASIS,
            "match_value": _text(row.get("match_value")),
            "match_confidence": round(_confidence(row.get("match_confidence")), 4),
        })

    return {
        "total": total,
        "bands": bands,
        "basis_breakdown": basis_breakdown,
        "weak_link_examples": weak_link_examples,
    }


def _band_shares(counts: Dict[str, int], total: int) -> Dict[str, float]:
    """Fraction per band, rounded to 4dp, summing to 1.0 across populated bands."""
    shares: Dict[str, float] = {}
    if total <= 0:
        return {name: 0.0 for name in BAND_NAMES}
    for name in BAND_NAMES:
        shares[name] = round(counts.get(name, 0) / total, 4)
    residual = round(1.0 - sum(shares.values()), 4)
    if residual:
        populated = [name for name in BAND_NAMES if counts.get(name, 0) > 0]
        if populated:
            target = max(populated, key=lambda name: counts.get(name, 0))
            shares[target] = round(shares[target] + residual, 4)
    return shares


def write_report(rows: List[Dict[str, Any]], path: str) -> str:
    """Write the band report as JSON to `path`; returns the absolute path."""
    report = build_report(rows)
    target = os.path.abspath(path)
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, default=str)
    return target


# ---------------------------------------------------------------- data seam


def fetch_link_rows(db: Session) -> List[Dict[str, Any]]:
    """Read link rows from the authoritative APP table (read-only)."""
    stmt = select(
        VulnLink.server_id,
        VulnLink.advisory_id,
        VulnLink.match_basis,
        VulnLink.match_value,
        VulnLink.match_confidence,
    )
    return [
        {
            "server_id": r[0],
            "advisory_id": r[1],
            "match_basis": r[2],
            "match_value": r[3],
            "match_confidence": r[4],
        }
        for r in db.execute(stmt).all()
    ]


def build_report_from_db(db: Session) -> Dict[str, Any]:
    """Band report over the real vuln_links rows (read-only)."""
    return build_report(fetch_link_rows(db))


@contextmanager
def _session_scope():
    """One real app session (read-only use), closed on exit."""
    provider = get_session()
    session = next(provider)
    try:
        yield session
    finally:
        session.close()


@router.get("/vuln-link-confidence-bands")
def vuln_link_confidence_bands(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Confidence-band profile of every advisory->server vulnerability link."""
    return build_report_from_db(db)


# ---------------------------------------------------------------- entrypoint


def run(rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Build the band report and print it as JSON.

    With no rows supplied the report is built from the real app tables.
    """
    if rows is None:
        with _session_scope() as session:
            report = build_report_from_db(session)
    else:
        report = build_report(rows)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Vuln link confidence bands report")
    parser.add_argument("--out", help="also write the JSON report to this path")
    args = parser.parse_args(argv)
    report = run()
    if args.out:
        write_report([], args.out)
        print("wrote %s" % os.path.abspath(args.out))
    return 0


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite session override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    ROWS = [
        {"server_id": "srv_a", "advisory_id": "CVE-2026-0001",
         "match_basis": "package_exact", "match_value": "left-pad",
         "match_confidence": 0.95},
        {"server_id": "srv_b", "advisory_id": "CVE-2026-0002",
         "match_basis": "repo_exact", "match_value": "org/repo",
         "match_confidence": 0.8},
        {"server_id": "srv_a", "advisory_id": "CVE-2026-0003",
         "match_basis": "package_exact", "match_value": "right-pad",
         "match_confidence": 0.79},
        {"server_id": "srv_b", "advisory_id": "CVE-2026-0001",
         "match_basis": "package_exact", "match_value": "center-pad",
         "match_confidence": 0.5},
        {"server_id": "srv_c", "advisory_id": "CVE-2026-0004",
         "match_basis": "repo_exact", "match_value": "org/other",
         "match_confidence": 0.6},
        {"server_id": "srv_c", "advisory_id": "CVE-2026-0005",
         "match_basis": "package_exact", "match_value": "ghost-pad",
         "match_confidence": 0.2},
    ]

    # --- fixture 1: the pure layer over 6 synthetic rows ---
    report = build_report(ROWS)
    assert report["total"] == 6, report
    bands = {b["band"]: b for b in report["bands"]}
    assert set(bands) == {"strong", "medium", "weak"}, report
    assert bands["strong"]["count"] == 2, report
    assert bands["medium"]["count"] == 3, report
    assert bands["weak"]["count"] == 1, report
    for band in report["bands"]:
        assert 0.0 <= band["share"] <= 1.0, report
    assert round(sum(b["share"] for b in report["bands"]), 4) == 1.0, report
    assert len(report["basis_breakdown"]) == 2, report
    assert {b["match_basis"] for b in report["basis_breakdown"]} == \
        {"package_exact", "repo_exact"}, report
    assert len(report["weak_link_examples"]) == 1, report
    assert report["weak_link_examples"][0]["advisory_id"] == "CVE-2026-0005", report
    assert bands["strong"]["distinct_servers"] == 2, report
    assert bands["strong"]["distinct_advisories"] == 2, report

    # --- fixture 2: empty input must not raise ---
    empty = build_report([])
    assert empty["total"] == 0, empty
    assert all(b["count"] == 0 and b["share"] == 0.0 for b in empty["bands"]), empty
    assert empty["weak_link_examples"] == [], empty

    # --- fixture 3: write_report writes JSON to disk and returns the path ---
    with tempfile.TemporaryDirectory() as tmp:
        target = write_report(ROWS, os.path.join(tmp, "bands.json"))
        assert os.path.exists(target), target
        with open(target, encoding="utf-8") as handle:
            saved = json.load(handle)
        assert saved["total"] == 6, saved

    # --- fixture 4: the real app data layer over a SQLite session ---
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(VulnLink(id=1, advisory_id="CVE-2026-0001", server_id="srv_a",
                   match_basis="package_exact", match_value="left-pad",
                   match_confidence=0.95))
    s.add(VulnLink(id=2, advisory_id="CVE-2026-0005", server_id="srv_c",
                   match_basis="package_exact", match_value="ghost-pad",
                   match_confidence=0.2))
    s.commit()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app)
    resp = client.get("/api/reports/vuln-link-confidence-bands")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["total"] == 2, payload
    db_bands = {b["band"]: b for b in payload["bands"]}
    assert db_bands["strong"]["count"] == 1, payload
    assert db_bands["weak"]["count"] == 1, payload
    assert payload["weak_link_examples"][0]["server_id"] == "srv_c", payload
    s.close()

    print("PASS")
