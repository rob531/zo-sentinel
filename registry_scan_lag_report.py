"""registry_scan_lag_report.py -- standalone staleness report over the server registry.

Reads mcp_server_registry (server_id, name, risk_tier, last_scanned, scan_count,
last_seen) through the REAL app data layer (app.db / app.models) and reports how
far behind the scan coverage is. No HTTP route, no service unit: the module is
imported and called directly.

Pure-read: no network, no DB writes, no import-time side effects.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

Bucket = Tuple[str, Optional[float]]

DEFAULT_LAG_BUCKETS: Tuple[Bucket, ...] = (
    ("never_scanned", None),
    ("within_24h", 1.0),
    ("within_7d", 7.0),
    ("within_30d", 30.0),
    ("over_30d", 31.0),
)

MAX_OFFENDERS = 50


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(last_scanned: Optional[datetime], now: datetime) -> Optional[float]:
    normalized = _as_utc(last_scanned)
    if normalized is None:
        return None
    return max(0.0, (now - normalized).total_seconds() / 86400.0)


def _bucket_label(age: Optional[float], buckets: Sequence[Bucket]) -> str:
    """Exclusive bucket: age <= threshold for the first matching threshold."""
    labels = [label for label, _ in buckets]
    if age is None:
        return labels[0] if labels else "never_scanned"
    fallback = labels[-1] if labels else "over_30d"
    for label, threshold in buckets:
        if threshold is None:
            continue
        if age <= threshold:
            return label
    return fallback


def _open_session(db: Optional[Session]) -> Tuple[Session, Any]:
    if db is not None:
        return db, None
    generator = get_session()
    return next(generator), generator.close


def build_scan_lag_report(
    now: Optional[datetime] = None,
    lag_buckets: Tuple[Bucket, ...] = DEFAULT_LAG_BUCKETS,
    db: Optional[Session] = None,
) -> dict:
    """Build the registry scan-lag report. `now` is the reference timestamp."""
    reference = _as_utc(now) or datetime.now(timezone.utc)
    session, closer = _open_session(db)
    try:
        statement = select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_scanned,
            McpServerRegistry.scan_count,
            McpServerRegistry.last_seen,
        )
        rows = session.execute(statement).all()
    finally:
        if closer is not None:
            closer()

    buckets: Dict[str, int] = {label: 0 for label, _ in lag_buckets}
    tier_counts: Dict[str, Dict[str, int]] = {}
    offenders: List[Dict[str, Any]] = []
    never_scanned_total = 0

    for row in rows:
        age = _age_days(row.last_scanned, reference)
        label = _bucket_label(age, lag_buckets)
        if label in buckets:
            buckets[label] += 1
        else:
            buckets[label] = 1

        tier = (row.risk_tier or "unknown").strip() or "unknown"
        tier_entry = tier_counts.setdefault(tier, {"count": 0, "never_scanned": 0})
        tier_entry["count"] += 1

        if age is None:
            never_scanned_total += 1
            tier_entry["never_scanned"] += 1
            continue

        scanned = _as_utc(row.last_scanned)
        offenders.append({
            "server_id": row.server_id,
            "name": row.name,
            "risk_tier": tier,
            "last_scanned": scanned.isoformat() if scanned else None,
            "days_since_scan": round(age, 3),
        })

    offenders.sort(key=lambda item: (-item["days_since_scan"], item["server_id"] or ""))
    offenders = offenders[:MAX_OFFENDERS]

    return {
        "generated_at": reference.isoformat(),
        "total_servers": len(rows),
        "buckets": buckets,
        "never_scanned_total": never_scanned_total,
        "tier_breakdown": tier_counts,
        "worst_offenders": offenders,
    }


def render_report(report: dict) -> str:
    """Plain-text table rendering of a report produced by build_scan_lag_report."""
    lines: List[str] = []
    lines.append("Registry Scan Lag Report")
    lines.append("=" * 60)
    lines.append("Generated at : {}".format(report.get("generated_at")))
    lines.append("Total servers: {}".format(report.get("total_servers", 0)))
    lines.append("Never scanned: {}".format(report.get("never_scanned_total", 0)))
    lines.append("")
    lines.append("Bucket counts")
    lines.append("-" * 60)
    buckets = report.get("buckets") or {}
    for label in sorted(buckets):
        lines.append("{:<20} {}".format(label, buckets[label]))
    lines.append("")
    lines.append("Risk tier breakdown")
    lines.append("-" * 60)
    tier_breakdown = report.get("tier_breakdown") or {}
    for tier in sorted(tier_breakdown):
        entry = tier_breakdown[tier] or {}
        lines.append("{:<20} count={:<8} never_scanned={}".format(
            tier, entry.get("count", 0), entry.get("never_scanned", 0)))
    lines.append("")
    lines.append("Worst offenders (oldest scans first)")
    lines.append("-" * 60)
    offenders = report.get("worst_offenders") or []
    if not offenders:
        lines.append("(none)")
    else:
        header = "{:<40} {:<14} {:>10}  {}".format("server_id", "risk_tier", "days", "last_scanned")
        lines.append(header)
        for item in offenders:
            lines.append("{:<40} {:<14} {:>10}  {}".format(
                str(item.get("server_id") or "")[:40],
                str(item.get("risk_tier") or "")[:14],
                item.get("days_since_scan"),
                item.get("last_scanned") or "-"))
    return "\n".join(lines)


if __name__ == "__main__":
    from datetime import timedelta
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import registry_scan_lag_report as module

    from app.models import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    reference = datetime(2026, 1, 31, 12, 0, 0, tzinfo=timezone.utc)
    session = TS()
    session.add(McpServerRegistry(server_id="srv-never", name="Never Scanned MCP",
                                  url="https://example.com/never",
                                  registry_source="user_submission",
                                  risk_tier="HIGH", scan_count=0))
    session.add(McpServerRegistry(server_id="srv-fresh", name="Freshly Scanned MCP",
                                  url="https://example.com/fresh",
                                  registry_source="official",
                                  risk_tier="LOW", scan_count=4,
                                  last_scanned=reference - timedelta(hours=1)))
    session.add(McpServerRegistry(server_id="srv-old", name="Old Scanned MCP",
                                  url="https://example.com/old",
                                  registry_source="official",
                                  risk_tier="CRITICAL", scan_count=9,
                                  last_scanned=reference - timedelta(days=45)))
    session.commit()

    def _override_session():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    module.get_session = _override_session  # same override seam as the app dependency

    report = module.build_scan_lag_report(now=reference)
    assert report["total_servers"] == 3, report
    assert sum(report["buckets"].values()) == 3, report
    assert report["never_scanned_total"] == 1, report
    assert report["buckets"]["never_scanned"] == 1, report
    assert report["buckets"]["within_24h"] == 1, report
    assert report["buckets"]["over_30d"] == 1, report
    assert report["worst_offenders"][0]["server_id"] == "srv-old", report
    assert report["worst_offenders"][0]["days_since_scan"] == 45.0, report
    assert report["tier_breakdown"]["HIGH"]["never_scanned"] == 1, report
    assert report["tier_breakdown"]["CRITICAL"]["count"] == 1, report

    text = module.render_report(report)
    assert "Registry Scan Lag Report" in text
    assert "srv-old" in text

    empty_engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                 poolclass=StaticPool)
    Base.metadata.create_all(empty_engine)
    empty_session = sessionmaker(bind=empty_engine, autoflush=False, autocommit=False)()
    empty = module.build_scan_lag_report(now=reference, db=empty_session)
    assert empty["total_servers"] == 0, empty
    assert empty["worst_offenders"] == [], empty
    assert "Worst offenders" in module.render_report(empty)

    session.close()
    print("PASS")
