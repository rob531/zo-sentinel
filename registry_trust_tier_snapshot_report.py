# deps: sqlalchemy
"""registry_trust_tier_snapshot_report.py -- point-in-time registry trust health snapshot.

Standalone report generator (no HTTP route). Reads the app data layer
(mcp_server_registry + mcp_llm_axis_scores) through the REAL app session
(app.db.get_session / app.models) and renders a deterministic Markdown report.

Pure helpers:
    build_registry_trust_tier_snapshot(servers, axis_scores, as_of) -> dict
    render_registry_trust_tier_snapshot(report) -> str

No DB writes, no network, no import-time side effects.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

VALID_AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

# Most attention-worthy first (INSUFFICIENT = unclassified -> elevated attention).
RISK_TIER_ORDER = (
    "KNOWN_THREAT",
    "HIGH_RISK_ISOLATED",
    "CAUTION_LIMITED",
    "INSUFFICIENT",
    "ENTERPRISE_CONTROLLED",
    "TRUSTED_RESEARCH",
    "TRUSTED_GENERAL",
)

TIER_RANK = {tier: index for index, tier in enumerate(RISK_TIER_ORDER)}

STALE_SCAN_DAYS = 30


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _percentile(values: Sequence[float], quantile: float) -> Optional[float]:
    """Linearly interpolated percentile (stdlib only)."""
    if not values:
        return None
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be between 0 and 1")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    lower_weight = upper - position
    upper_weight = position - lower
    return ordered[lower] * lower_weight + ordered[upper] * upper_weight


def _median(values: Sequence[float]) -> Optional[float]:
    return _percentile(values, 0.50)


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> Optional[str]:
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sorted_tiers(counts: Mapping[str, int]) -> dict:
    known = [tier for tier in RISK_TIER_ORDER if tier in counts]
    extra = sorted(tier for tier in counts if tier not in RISK_TIER_ORDER)
    return {tier: counts[tier] for tier in known + extra}


def build_registry_trust_tier_snapshot(
    servers: list[dict],
    axis_scores: list[dict],
    as_of: str,
) -> dict:
    """Build the trust-health snapshot dict from registry + axis-score rows."""
    reference = _parse_dt(as_of) or datetime.now(timezone.utc)
    stale_cutoff = reference - timedelta(days=STALE_SCAN_DAYS)

    tier_counts: dict[str, int] = {}
    verdict_counts: dict[str, int] = {}
    scored_server_ids: set[str] = set()
    axis_values: dict[str, list[float]] = {axis: [] for axis in VALID_AXIS_NAMES}
    axis_servers: dict[str, set[str]] = {axis: set() for axis in VALID_AXIS_NAMES}

    for row in axis_scores or []:
        server_id = str(row.get("server_id") or "")
        if server_id:
            scored_server_ids.add(server_id)
        axis_name = str(row.get("axis_name") or "").strip()
        p_top = _as_float(row.get("p_top"))
        if axis_name in axis_values:
            if server_id:
                axis_servers[axis_name].add(server_id)
            if p_top is not None:
                axis_values[axis_name].append(p_top)

    stale_scans = 0
    never_scanned = 0
    unscored_servers = 0
    risky: list[dict] = []
    total_servers = 0

    for row in servers or []:
        total_servers += 1
        server_id = str(row.get("server_id") or "")
        risk_tier = str(row.get("risk_tier") or "INSUFFICIENT").strip().upper() or "INSUFFICIENT"
        tier_counts[risk_tier] = tier_counts.get(risk_tier, 0) + 1
        verdict = str(row.get("verdict") or "unreviewed").strip().lower() or "unreviewed"
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

        if server_id not in scored_server_ids:
            unscored_servers += 1

        last_scanned = _parse_dt(row.get("last_scanned"))
        if last_scanned is None:
            never_scanned += 1
            stale_scans += 1
        elif last_scanned < stale_cutoff:
            stale_scans += 1

        trust_score = _as_float(row.get("trust_score"))
        risky.append({
            "server_id": server_id,
            "name": row.get("name"),
            "risk_tier": risk_tier,
            "trust_score": trust_score,
        })

    tier_pct = {
        tier: (100.0 * count / total_servers if total_servers else 0.0)
        for tier, count in tier_counts.items()
    }

    risky.sort(key=lambda item: (
        TIER_RANK.get(item["risk_tier"], len(RISK_TIER_ORDER)),
        item["trust_score"] if item["trust_score"] is not None else -1.0,
        item["server_id"],
    ))

    axis_coverage = {}
    for axis in VALID_AXIS_NAMES:
        values = axis_values[axis]
        axis_coverage[axis] = {
            "present": len(axis_servers[axis]),
            "median_p_top": _median(values),
            "p90_p_top": _percentile(values, 0.90),
        }

    return {
        "as_of": _iso(reference) or str(as_of),
        "total_servers": total_servers,
        "tier_counts": _sorted_tiers(tier_counts),
        "tier_pct": tier_pct,
        "verdict_counts": dict(sorted(verdict_counts.items())),
        "unscored_servers": unscored_servers,
        "stale_scans": stale_scans,
        "never_scanned": never_scanned,
        "axis_coverage": axis_coverage,
        "top_10_riskiest": risky[:10],
    }


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return format(value, ".6g")
    return str(value).replace("|", "\\|")


def render_registry_trust_tier_snapshot(report: dict) -> str:
    """Render the snapshot dict as Markdown."""
    lines = ["# Registry Trust Tier Snapshot", ""]
    lines.append(f"- As of: `{report.get('as_of')}`")
    lines.append(f"- Total servers: `{report.get('total_servers')}`")
    lines.append(f"- Unscored servers: `{report.get('unscored_servers')}`")
    lines.append(f"- Stale scans (> {STALE_SCAN_DAYS} days): `{report.get('stale_scans')}`")
    lines.append("")

    lines += ["## Risk tier counts", "",
              "| tier | count | pct |", "| --- | --- | --- |"]
    tier_counts = report.get("tier_counts") or {}
    tier_pct = report.get("tier_pct") or {}
    if tier_counts:
        for tier, count in tier_counts.items():
            lines.append(f"| {_fmt(tier)} | {_fmt(count)} | {_fmt(tier_pct.get(tier))} |")
    else:
        lines.append("| _none_ | 0 | 0 |")
    lines.append("")

    lines += ["## Verdict counts", "",
              "| verdict | count |", "| --- | --- |"]
    verdict_counts = report.get("verdict_counts") or {}
    if verdict_counts:
        for verdict, count in verdict_counts.items():
            lines.append(f"| {_fmt(verdict)} | {_fmt(count)} |")
    else:
        lines.append("| _none_ | 0 |")
    lines.append("")

    lines += ["## Axis coverage", "",
              "| axis | present | median_p_top | p90_p_top |",
              "| --- | --- | --- | --- |"]
    coverage = report.get("axis_coverage") or {}
    if coverage:
        for axis, stats in coverage.items():
            lines.append(
                f"| {_fmt(axis)} | {_fmt(stats.get('present'))} | "
                f"{_fmt(stats.get('median_p_top'))} | {_fmt(stats.get('p90_p_top'))} |"
            )
    else:
        lines.append("| _none_ | 0 |  |  |")
    lines.append("")

    lines += ["## Top 10 riskiest servers", "",
              "| server_id | name | risk_tier | trust_score |",
              "| --- | --- | --- | --- |"]
    risky = report.get("top_10_riskiest") or []
    if risky:
        for item in risky:
            lines.append(
                f"| {_fmt(item.get('server_id'))} | {_fmt(item.get('name'))} | "
                f"{_fmt(item.get('risk_tier'))} | {_fmt(item.get('trust_score'))} |"
            )
    else:
        lines.append("| _none_ |  |  |  |")
    lines.append("")

    return "\n".join(lines)


def load_snapshot_inputs(db: Session) -> tuple[list[dict], list[dict]]:
    """Read registry + axis-score rows through the real app session."""
    server_rows = db.execute(select(McpServerRegistry)).scalars().all()
    servers = [
        {
            "server_id": row.server_id,
            "name": row.name,
            "risk_tier": row.risk_tier,
            "trust_score": row.trust_score,
            "verdict": row.verdict,
            "last_assessed": _iso(row.last_assessed),
            "last_scanned": _iso(row.last_scanned),
            "scan_count": row.scan_count,
        }
        for row in server_rows
    ]

    score_rows = db.execute(select(McpLlmAxisScore)).scalars().all()
    axis_scores = [
        {
            "server_id": row.server_id,
            "axis_name": row.axis_name,
            "p_top": row.p_top,
            "scored_at": _iso(row.scored_at),
        }
        for row in score_rows
    ]
    return servers, axis_scores


def build_snapshot_from_db(db: Session, as_of: Optional[str] = None) -> dict:
    """Load rows from the app DB and build the snapshot."""
    servers, axis_scores = load_snapshot_inputs(db)
    stamp = as_of or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return build_registry_trust_tier_snapshot(servers, axis_scores, stamp)


# --------------------------------------------------------------------------
# Self-test fixtures (pure-function level; the DB path is exercised below)
# --------------------------------------------------------------------------
def _fixture_servers() -> list[dict]:
    return [
        {
            "server_id": "srv-alpha",
            "name": "Alpha MCP",
            "risk_tier": "HIGH_RISK_ISOLATED",
            "trust_score": 0.2,
            "verdict": "HIGH",
            "last_assessed": "2026-11-01T00:00:00Z",
            "last_scanned": "2026-11-01T00:00:00Z",
            "scan_count": 7,
        },
        {
            "server_id": "srv-beta",
            "name": "Beta MCP",
            "risk_tier": "TRUSTED_GENERAL",
            "trust_score": 0.95,
            "verdict": "LOW",
            "last_assessed": "2026-11-01T00:00:00Z",
            "last_scanned": "2026-09-01T00:00:00Z",
            "scan_count": 2,
        },
        {
            "server_id": "srv-gamma",
            "name": "Gamma MCP",
            "risk_tier": "INSUFFICIENT",
            "trust_score": None,
            "verdict": "unreviewed",
            "last_assessed": None,
            "last_scanned": None,
            "scan_count": 0,
        },
    ]


def _fixture_axis_scores() -> list[dict]:
    return [
        {"server_id": "srv-alpha", "axis_name": "overall_risk", "p_top": 0.8,
         "scored_at": "2026-11-01T00:00:00Z"},
        {"server_id": "srv-alpha", "axis_name": "auth_strength", "p_top": 0.2,
         "scored_at": "2026-11-01T00:00:00Z"},
        {"server_id": "srv-alpha", "axis_name": "data_sensitivity", "p_top": 0.5,
         "scored_at": "2026-11-01T00:00:00Z"},
        {"server_id": "srv-beta", "axis_name": "overall_risk", "p_top": 0.05,
         "scored_at": "2026-11-01T00:00:00Z"},
    ]


def _self_test() -> None:
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    as_of = "2026-11-10T00:00:00Z"

    # --- fixture 1: three servers, two scored ---
    report = build_registry_trust_tier_snapshot(
        _fixture_servers(), _fixture_axis_scores(), as_of)
    assert report["total_servers"] == 3, report
    assert sum(report["tier_counts"].values()) == 3, report
    assert report["tier_counts"]["HIGH_RISK_ISOLATED"] == 1, report
    assert report["tier_counts"]["TRUSTED_GENERAL"] == 1, report
    assert report["tier_counts"]["INSUFFICIENT"] == 1, report
    assert report["unscored_servers"] == 1, report
    assert report["stale_scans"] == 2, report
    assert report["never_scanned"] == 1, report
    assert report["verdict_counts"]["high"] == 1, report
    assert report["axis_coverage"]["overall_risk"]["present"] == 2, report
    assert math.isclose(report["axis_coverage"]["overall_risk"]["median_p_top"], 0.425), report
    assert math.isclose(report["axis_coverage"]["auth_strength"]["median_p_top"], 0.2), report
    assert report["top_10_riskiest"][0]["server_id"] == "srv-alpha", report
    assert report["top_10_riskiest"][1]["server_id"] == "srv-gamma", report

    markdown = render_registry_trust_tier_snapshot(report)
    assert "tier" in markdown.lower(), markdown
    assert "HIGH_RISK_ISOLATED" in markdown, markdown
    assert "Axis coverage" in markdown, markdown

    # --- fixture 2: single scored server, no stale scans ---
    one_server = [{
        "server_id": "srv-only",
        "name": "Only MCP",
        "risk_tier": "TRUSTED_RESEARCH",
        "trust_score": 0.8,
        "verdict": "LOW",
        "last_assessed": "2026-11-09T00:00:00Z",
        "last_scanned": "2026-11-09T00:00:00Z",
        "scan_count": 4,
    }]
    one_axis = [{
        "server_id": "srv-only",
        "axis_name": "overall_risk",
        "p_top": 0.1,
        "scored_at": "2026-11-09T00:00:00Z",
    }]
    report2 = build_registry_trust_tier_snapshot(one_server, one_axis, as_of)
    assert report2["total_servers"] == 1, report2
    assert report2["unscored_servers"] == 0, report2
    assert report2["stale_scans"] == 0, report2
    assert report2["tier_counts"]["TRUSTED_RESEARCH"] == 1, report2
    assert math.isclose(report2["axis_coverage"]["overall_risk"]["median_p_top"], 0.1), report2
    assert len(report2["top_10_riskiest"]) == 1, report2

    # --- fixture 3: empty inputs (edge case) ---
    empty = build_registry_trust_tier_snapshot([], [], as_of)
    assert empty["total_servers"] == 0, empty
    assert empty["unscored_servers"] == 0, empty
    assert empty["stale_scans"] == 0, empty
    assert empty["tier_counts"] == {}, empty
    assert empty["top_10_riskiest"] == [], empty
    assert "tier" in render_registry_trust_tier_snapshot(empty).lower()

    # --- real app data layer (SQLite via dependency override) ---
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TS()
    session.add(McpServerRegistry(
        server_id="srv1", name="Alpha MCP", url="https://github.com/acme/alpha",
        registry_source="github", risk_tier="HIGH_RISK_ISOLATED", trust_score=0.2,
        verdict="HIGH", last_assessed=datetime(2026, 11, 1, tzinfo=timezone.utc),
        last_scanned=datetime(2026, 11, 1, tzinfo=timezone.utc), scan_count=7))
    session.add(McpServerRegistry(
        server_id="srv2", name="Beta MCP", url="https://github.com/acme/beta",
        registry_source="github", risk_tier="TRUSTED_GENERAL", trust_score=0.95,
        verdict="LOW", last_assessed=datetime(2026, 11, 1, tzinfo=timezone.utc),
        last_scanned=datetime(2026, 9, 1, tzinfo=timezone.utc), scan_count=2))
    session.add(McpServerRegistry(
        server_id="srv3", name="Gamma MCP", url="https://github.com/acme/gamma",
        registry_source="npm", risk_tier="INSUFFICIENT", trust_score=None,
        verdict="unreviewed", last_assessed=None, last_scanned=None, scan_count=0))
    for index, (axis, label) in enumerate(
        (("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
         ("data_sensitivity", "MODERATE")), start=1):
        session.add(McpLlmAxisScore(id=index, server_id="srv1", axis_name=axis,
                                    label=label, model_version="v3.0_40974559"))
    session.add(McpLlmAxisScore(id=10, server_id="srv2", axis_name="overall_risk",
                                label="LOW", model_version="v3.0_40974559"))
    session.commit()
    session.close()

    app = FastAPI()

    def _override_session():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _override_session
    db_session = TS()
    try:
        servers, axis_scores = load_snapshot_inputs(db_session)
        assert len(servers) == 3, servers
        assert len(axis_scores) == 4, axis_scores
        db_report = build_registry_trust_tier_snapshot(servers, axis_scores, as_of)
        assert db_report["total_servers"] == 3, db_report
        assert db_report["unscored_servers"] == 1, db_report
        assert sum(db_report["tier_counts"].values()) == 3, db_report
        assert db_report["axis_coverage"]["overall_risk"]["present"] == 2, db_report
        assert "tier" in render_registry_trust_tier_snapshot(db_report).lower()
    finally:
        db_session.close()


if __name__ == "__main__":
    _self_test()
    print("PASS")
