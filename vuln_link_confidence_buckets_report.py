"""vuln_link_confidence_buckets_report.py -- confidence/quality rollup for vuln_links.

Offline operator report (NO HTTP route): buckets every deterministic advisory->
server linkage by match_confidence and summarises the match_basis mix.

Standalone report generator:
    bucket_confidence(value: float) -> str
    build_report(rows: list[dict]) -> str
    main() -> int

Data access is READ-ONLY through the app SQLAlchemy session (app.db.get_session +
app.models.VulnLink). No writes, no network, no DELETE/DROP/TRUNCATE.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink

BUCKETS = ("0.0-0.5", "0.5-0.7", "0.7-0.9", "0.9-1.0")


def bucket_confidence(value: float) -> str:
    """Map a match_confidence value onto one of the four quality buckets."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return BUCKETS[0]
    if v < 0.5:
        return BUCKETS[0]
    if v < 0.7:
        return BUCKETS[1]
    if v < 0.9:
        return BUCKETS[2]
    return BUCKETS[3]


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def fetch_rows(db: Session) -> List[Dict[str, Any]]:
    """Read vuln_links (read-only) into the report row shape."""
    rows = db.execute(select(VulnLink)).scalars().all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        linked_at = _row_get(r, "linked_at")
        out.append({
            "match_basis": _row_get(r, "match_basis") or "unknown",
            "match_confidence": float(_row_get(r, "match_confidence") or 0.0),
            "linked_at": linked_at.isoformat() if hasattr(linked_at, "isoformat") else "",
        })
    return out


def build_report(rows: Iterable[Dict[str, Any]]) -> str:
    """Render the Markdown confidence/quality rollup."""
    rows = list(rows or [])
    total = len(rows)

    bucket_counts: Dict[str, int] = {b: 0 for b in BUCKETS}
    basis_counts: Dict[str, int] = {}
    basis_conf_sum: Dict[str, float] = {}
    for row in rows:
        bucket_counts[bucket_confidence(_row_get(row, "match_confidence", 0.0))] += 1
        basis = str(_row_get(row, "match_basis") or "unknown")
        basis_counts[basis] = basis_counts.get(basis, 0) + 1
        try:
            conf = float(_row_get(row, "match_confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        basis_conf_sum[basis] = basis_conf_sum.get(basis, 0.0) + conf

    lines: List[str] = []
    lines.append("# Vuln Link Confidence Buckets")
    lines.append("")
    lines.append(f"Generated at: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Total links | {total} |")
    lines.append("")
    lines.append("## Confidence buckets")
    lines.append("")
    lines.append("| Bucket | Count | Pct |")
    lines.append("| --- | --- | --- |")
    for b in BUCKETS:
        count = bucket_counts.get(b, 0)
        pct = round((count / total) * 100.0, 1) if total else 0.0
        lines.append(f"| {b} | {count} | {pct}% |")
    lines.append("")
    lines.append("## Match basis")
    lines.append("")
    lines.append("| Match basis | Count | Avg confidence |")
    lines.append("| --- | --- | --- |")
    for basis in sorted(basis_counts):
        count = basis_counts[basis]
        avg = (basis_conf_sum[basis] / count) if count else 0.0
        lines.append(f"| {basis} | {count} | {round(avg, 3)} |")
    lines.append("")
    lines.append(f"_Generated at {datetime.now(timezone.utc).isoformat()}_")
    return "\n".join(lines) + "\n"


def main() -> int:
    """Run the rollup against the live app DB (read-only) and print it."""
    try:
        db = next(get_session())
    except Exception as exc:
        print(f"vuln link confidence report unavailable: {exc}")
        return 1
    try:
        rows = fetch_rows(db)
    except Exception as exc:
        print(f"vuln link confidence report failed: {exc}")
        return 1
    finally:
        try:
            db.close()
        except Exception:
            pass
    print(build_report(rows))
    return 0


def _self_test() -> None:
    rows = [
        {"match_basis": "repo_exact", "match_confidence": 0.2,
         "linked_at": "2026-01-01T00:00:00+00:00"},
        {"match_basis": "repo_exact", "match_confidence": 0.45,
         "linked_at": "2026-01-02T00:00:00+00:00"},
        {"match_basis": "package_exact", "match_confidence": 0.6,
         "linked_at": "2026-01-03T00:00:00+00:00"},
        {"match_basis": "package_exact", "match_confidence": 0.8,
         "linked_at": "2026-01-04T00:00:00+00:00"},
        {"match_basis": "repo_exact", "match_confidence": 0.95,
         "linked_at": "2026-01-05T00:00:00+00:00"},
        {"match_basis": "package_exact", "match_confidence": 1.0,
         "linked_at": "2026-01-06T00:00:00+00:00"},
    ]

    assert bucket_confidence(0.2) == "0.0-0.5"
    assert bucket_confidence(0.6) == "0.5-0.7"
    assert bucket_confidence(0.8) == "0.7-0.9"
    assert bucket_confidence(0.95) == "0.9-1.0"
    assert bucket_confidence(1.0) == "0.9-1.0"

    report = build_report(rows)
    assert report, "report must not be empty"
    assert "| Total links | 6 |" in report, report
    assert "0.0-0.5" in report, report
    assert "# Vuln Link Confidence Buckets" in report, report
    assert report.endswith("\n"), repr(report[-20:])
    assert "| repo_exact | 3 |" in report, report
    assert "| package_exact | 3 |" in report, report
    assert build_report([]).endswith("\n")
    print("PASS")


if __name__ == "__main__":
    _self_test()
