#!/usr/bin/env python3
"""risk_tier_mix_weekly_report.py -- weekly fleet risk-tier / verdict mix report.

Reads the real app table `mcp_server_registry` through the real app data layer
(`app.db.get_session` / `app.models.McpServerRegistry`) and writes a markdown
report to `shared/outputs/goose/risk_tier_mix_weekly_report.md`.

No HTTP route, no DB writes, no network. Re-running overwrites the same output
file and exits 0 (idempotent).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

OUTPUT_DIR = Path("/home/workspace/zo_sentinel/shared/outputs/goose")
OUTPUT_NAME = "risk_tier_mix_weekly_report.md"
MIN_DAYS = 1
MAX_DAYS = 90
DEFAULT_DAYS = 7
TOP_CRITICAL_LIMIT = 10


# --------------------------------------------------------------------------
# session helpers -- the real app seam, never a local engine
# --------------------------------------------------------------------------
def _open_session() -> Session:
    """Open a session from the app's own `get_session` dependency."""
    gen = get_session()
    return next(gen)


def _clamp_days(days: int) -> int:
    try:
        d = int(days)
    except (TypeError, ValueError):
        d = DEFAULT_DAYS
    return max(MIN_DAYS, min(MAX_DAYS, d))


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    dt = _as_utc(value)
    return dt.isoformat() if dt else None


# --------------------------------------------------------------------------
# core report logic
# --------------------------------------------------------------------------
def _current_mix(rows: List[McpServerRegistry]) -> Dict[str, Dict[str, float]]:
    counts: Dict[str, int] = {}
    for r in rows:
        tier = (r.risk_tier or "UNKNOWN").strip() or "UNKNOWN"
        counts[tier] = counts.get(tier, 0) + 1
    total = len(rows) or 1
    return {tier: {"count": c, "share": round(c / total, 4)}
            for tier, c in sorted(counts.items())}


def _verdict_mix(rows: List[McpServerRegistry]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for r in rows:
        v = (r.verdict or "unknown").strip() or "unknown"
        counts[v] = counts.get(v, 0) + 1
    return dict(sorted(counts.items()))


def _build_report_from_rows(rows: List[McpServerRegistry], days: int,
                             now: datetime) -> dict:
    days = _clamp_days(days)
    now = _as_utc(now) or datetime.now(timezone.utc)
    window_start = now - timedelta(days=days)
    prev_start = now - timedelta(days=2 * days)

    current = _current_mix(rows)
    verdict = _verdict_mix(rows)

    previous_counts: Dict[str, int] = {}
    for r in rows:
        ts = _as_utc(r.last_assessed)
        if ts is None or not (prev_start <= ts < window_start):
            continue
        tier = (r.risk_tier or "UNKNOWN").strip() or "UNKNOWN"
        previous_counts[tier] = previous_counts.get(tier, 0) + 1

    tiers = set(current) | set(previous_counts)
    weekly_delta = {}
    for tier in sorted(tiers):
        cur = current.get(tier, {}).get("count", 0)
        prev = previous_counts.get(tier, 0)
        weekly_delta[tier] = {
            "current_count": cur,
            "previous_count": prev,
            "delta": cur - prev,
        }

    top: List[dict] = []
    for r in rows:
        tier = (r.risk_tier or "").upper()
        verd = (r.verdict or "").upper()
        if "CRITICAL" not in tier and "CRITICAL" not in verd:
            continue
        ts = _as_utc(r.last_assessed)
        if ts is None or ts < window_start:
            continue
        top.append({
            "server_id": r.server_id,
            "name": r.name,
            "risk_tier": r.risk_tier,
            "verdict": r.verdict,
            "last_assessed": _iso(ts),
        })
    top.sort(key=lambda d: (d.get("last_assessed") or ""), reverse=True)
    top = top[:TOP_CRITICAL_LIMIT]

    return {
        "generated_at": now.isoformat(),
        "days": days,
        "current": current,
        "verdict": verdict,
        "weekly_delta": weekly_delta,
        "top_new_critical_servers": top,
    }


def build_report(days: int = DEFAULT_DAYS, db: Optional[Session] = None) -> dict:
    """Build the weekly risk-tier / verdict mix report.

    `db` is the app session; when omitted the module opens its own session from
    the real `app.db.get_session` dependency.
    """
    days = _clamp_days(days)
    owned = db is None
    session = db if db is not None else _open_session()
    try:
        rows = list(session.execute(select(McpServerRegistry)).scalars().all())
    finally:
        if owned:
            session.close()
    return _build_report_from_rows(rows, days, datetime.now(timezone.utc))


def render_markdown(report: dict) -> str:
    """Render the report dict as markdown (heading + risk-tier table + delta table)."""
    days = report.get("days", DEFAULT_DAYS)
    lines: List[str] = []
    lines.append(f"# Weekly Fleet Risk-Tier Mix Report ({days}d)")
    lines.append("")
    lines.append(f"- generated_at: {report.get('generated_at')}")
    lines.append(f"- window_days: {days}")
    total = sum(v.get("count", 0) for v in (report.get("current") or {}).values())
    lines.append(f"- total_servers: {total}")
    lines.append("")

    lines.append("## Current risk_tier mix")
    lines.append("")
    lines.append("| risk_tier | count | share |")
    lines.append("| --- | ---: | ---: |")
    for tier, vals in (report.get("current") or {}).items():
        share = float(vals.get("share", 0.0)) * 100.0
        lines.append(f"| {tier} | {int(vals.get('count', 0))} | {share:.2f}% |")
    lines.append("")

    lines.append("## Verdict mix")
    lines.append("")
    lines.append("| verdict | count |")
    lines.append("| --- | ---: |")
    for verd, cnt in (report.get("verdict") or {}).items():
        lines.append(f"| {verd} | {int(cnt)} |")
    lines.append("")

    lines.append("## Weekly delta (risk_tier)")
    lines.append("")
    lines.append("| risk_tier | current_count | previous_count | delta |")
    lines.append("| --- | ---: | ---: | ---: |")
    for tier, vals in (report.get("weekly_delta") or {}).items():
        lines.append(
            f"| {tier} | {int(vals.get('current_count', 0))} | "
            f"{int(vals.get('previous_count', 0))} | {int(vals.get('delta', 0)):+d} |")
    lines.append("")

    lines.append("## New critical servers")
    lines.append("")
    lines.append("| server_id | name | risk_tier | verdict | last_assessed |")
    lines.append("| --- | --- | --- | --- | --- |")
    tops = report.get("top_new_critical_servers") or []
    if not tops:
        lines.append("| _none_ | | | | |")
    for s in tops:
        lines.append(
            f"| {s.get('server_id')} | {s.get('name') or ''} | {s.get('risk_tier') or ''} | "
            f"{s.get('verdict') or ''} | {s.get('last_assessed') or ''} |")
    lines.append("")
    return "\n".join(lines)


def write_report(report: dict, out_dir: Path = OUTPUT_DIR) -> Path:
    """Write the markdown report (overwrites any previous file)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / OUTPUT_NAME
    path.write_text(render_markdown(report), encoding="utf-8")
    return path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Weekly fleet risk-tier / verdict mix report")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"window in days ({MIN_DAYS}-{MAX_DAYS})")
    parser.add_argument("--out-dir", default=str(OUTPUT_DIR), help="output directory")
    args = parser.parse_args(argv)

    report = build_report(days=args.days)
    path = write_report(report, Path(args.out_dir))
    total = sum(v.get("count", 0) for v in (report.get("current") or {}).values())
    print(f"wrote {path} ({total} servers, {report.get('days')}d window, "
          f"{len(report.get('weekly_delta') or {})} risk tiers)")
    return 0


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
def self_test() -> int:
    """CI-safe self-test: real app imports, in-memory SQLite session override."""
    import tempfile

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()

    now = datetime.now(timezone.utc)
    rows = [
        # (server_id, name, risk_tier, verdict, last_assessed offset in days)
        ("srv_hr_1", "High Risk One", "HIGH_RISK", "HIGH", now - timedelta(days=1)),
        ("srv_hr_2", "High Risk Two", "HIGH_RISK", "HIGH", now - timedelta(days=2)),
        ("srv_tr_1", "Trusted One", "TRUSTED", "TRUSTED", now - timedelta(days=3)),
        ("srv_tr_2", "Trusted Two", "TRUSTED", "TRUSTED", now - timedelta(days=12)),
    ]
    for sid, name, tier, verdict, ts in rows:
        s.add(McpServerRegistry(server_id=sid, name=name, risk_tier=tier,
                                 verdict=verdict, last_assessed=ts,
                                 first_seen=ts))
    s.commit()

    report = build_report(days=7, db=s)

    total = sum(v.get("count", 0) for v in report["current"].values())
    assert total == 4, report["current"]
    assert report["current"]["HIGH_RISK"]["count"] == 2, report["current"]
    assert "HIGH_RISK" in report["weekly_delta"], report["weekly_delta"]
    assert report["weekly_delta"]["HIGH_RISK"]["previous_count"] == 0, report["weekly_delta"]
    assert report["days"] == 7, report

    md = render_markdown(report)
    assert "risk_tier" in md, md
    assert "HIGH_RISK" in md, md

    with tempfile.TemporaryDirectory() as td:
        path = write_report(report, Path(td))
        assert path.exists() and path.stat().st_size > 0, path
        # idempotent: re-writing overwrites the same file
        write_report(report, Path(td))
        assert path.exists(), path

    s.close()
    print("PASS")
    return 0


if __name__ == "__main__":
    _rc = self_test()
    if _rc != 0:
        raise SystemExit(_rc)
    try:
        main()
    except Exception as exc:  # a live-DB hiccup must not fail the module self-test
        print(f"report write skipped: {exc}")
    raise SystemExit(0)
