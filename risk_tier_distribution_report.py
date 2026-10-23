"""risk_tier_distribution_report.py -- fleet risk-tier distribution report (read-only).

Summarises the LLM-owned scoring tables for dashboard/report consumption:
  * risk-tier distribution over the registry (mcp_server_registry.risk_tier)
  * per-axis p_top percentiles over the LLM axis scores (mcp_llm_axis_scores)
  * escalation count over the legal axes only

Read-only: no DB writes, no network, no file writes (stdout only). The report is
built from an injected SQLAlchemy session (the app Postgres data layer); the
self-test in __main__ supplies an in-memory SQLite session instead.

Only the 7 legal axis names are recognised; rows with any other axis_name are
ignored everywhere (percentiles AND escalation count).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")
_LEGAL_AXES = frozenset(AXES)

UNSCORED_TIER = "UNSCORED"


def _percentile(sorted_vals: List[float], q: float) -> Optional[float]:
    """Linear-interpolated percentile of an already-sorted list (q in 0..100)."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = (len(sorted_vals) - 1) * (q / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac)


def _tier_counts(db: Session) -> Dict[str, int]:
    rows = db.execute(select(McpServerRegistry.risk_tier)).scalars().all()
    counts: Dict[str, int] = {}
    for tier in rows:
        key = (tier or "").strip() or UNSCORED_TIER
        counts[key] = counts.get(key, 0) + 1
    return counts


def _axis_stats(db: Session) -> Dict[str, Dict[str, Optional[float]]]:
    rows = db.execute(
        select(McpLlmAxisScore.axis_name, McpLlmAxisScore.p_top)
        .where(McpLlmAxisScore.axis_name.in_(AXES))
    ).all()
    by_axis: Dict[str, List[float]] = {}
    for axis_name, p_top in rows:
        if axis_name not in _LEGAL_AXES:
            continue
        if p_top is None:
            continue
        by_axis.setdefault(axis_name, []).append(float(p_top))
    stats: Dict[str, Dict[str, Optional[float]]] = {}
    for axis_name, values in by_axis.items():
        values.sort()
        stats[axis_name] = {
            "count": len(values),
            "p50": _percentile(values, 50),
            "p90": _percentile(values, 90),
            "p99": _percentile(values, 99),
        }
    return stats


def _escalated_count(db: Session) -> int:
    rows = db.execute(
        select(McpLlmAxisScore.escalated)
        .where(McpLlmAxisScore.axis_name.in_(AXES),
               McpLlmAxisScore.escalated.is_(True))
    ).scalars().all()
    return len(rows)


def build_report(db_session: Session) -> dict:
    """Build the fleet risk-tier distribution report from the app data layer.

    Returns {generated_at, total_servers, tier_counts, tier_pct, axis_stats,
             escalated_count}. Read-only -- the session is never committed.
    """
    tier_counts = _tier_counts(db_session)
    total_servers = sum(tier_counts.values())
    tier_pct = {
        tier: round(100.0 * count / total_servers, 2) if total_servers else 0.0
        for tier, count in tier_counts.items()
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_servers": total_servers,
        "tier_counts": tier_counts,
        "tier_pct": tier_pct,
        "axis_stats": _axis_stats(db_session),
        "escalated_count": _escalated_count(db_session),
    }


@contextmanager
def _session_scope() -> Iterator[Session]:
    """Session from the app dependency (no writes; closed on exit)."""
    gen = get_session()
    session = next(gen)
    try:
        yield session
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def generate_report() -> dict:
    """Convenience wrapper: build the report from the app's own session."""
    with _session_scope() as session:
        return build_report(session)


def format_report(report: dict) -> str:
    """Human-readable rendering of the report dict (stdout only)."""
    lines = ["FLEET RISK TIER DISTRIBUTION",
             f"generated_at: {report.get('generated_at')}",
             f"total_servers: {report.get('total_servers', 0)}",
             f"escalated_count: {report.get('escalated_count', 0)}",
             "",
             f"{'RISK TIER':<24}{'COUNT':>8}{'PCT':>10}"]
    counts = report.get("tier_counts") or {}
    pcts = report.get("tier_pct") or {}
    for tier in sorted(counts):
        lines.append(f"{tier:<24}{counts[tier]:>8}{pcts.get(tier, 0.0):>10.2f}")
    lines += ["", f"{'AXIS':<24}{'COUNT':>8}{'P50':>10}{'P90':>10}{'P99':>10}"]
    stats = report.get("axis_stats") or {}
    for axis in sorted(stats):
        st = stats[axis]
        lines.append(f"{axis:<24}{st.get('count', 0):>8}"
                     f"{(st.get('p50') or 0.0):>10.4f}"
                     f"{(st.get('p90') or 0.0):>10.4f}"
                     f"{(st.get('p99') or 0.0):>10.4f}")
    return "\n".join(lines)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()

    fleet = [("srv1", "TRUSTED_GENERAL"), ("srv2", "CAUTION_LIMITED"),
             ("srv3", "HIGH_RISK_ISOLATED")]
    for sid, tier in fleet:
        s.add(McpServerRegistry(server_id=sid, name=f"Server {sid}",
                                url=f"https://example.com/{sid}",
                                registry_source="test", risk_tier=tier,
                                trust_score=0.5))
    i = 1
    for n, (sid, _tier) in enumerate(fleet):
        for ax, p_top, esc in (("overall_risk", 0.10 + 0.30 * n, True),
                               ("auth_strength", 0.20 + 0.30 * n, False)):
            s.add(McpLlmAxisScore(id=i, server_id=sid, axis_name=ax, label="X",
                                  model_version="v3.0_40974559",
                                  p_top=p_top, escalated=esc))
            i += 1
    # unknown axis -> must be ignored (not in axis_stats, not escalated)
    s.add(McpLlmAxisScore(id=i, server_id="srv1", axis_name="not_a_real_axis",
                          label="X", model_version="v3.0_40974559",
                          p_top=0.99, escalated=True))
    s.commit()

    rep = build_report(s)
    s.close()

    assert rep["total_servers"] == 3, rep
    assert sum(rep["tier_counts"].values()) == 3, rep
    assert rep["tier_counts"]["TRUSTED_GENERAL"] == 1, rep
    assert rep["tier_counts"]["CAUTION_LIMITED"] == 1, rep
    assert rep["tier_counts"]["HIGH_RISK_ISOLATED"] == 1, rep
    assert set(rep["tier_pct"]) == set(rep["tier_counts"]), rep
    assert set(rep["axis_stats"]).issubset(set(AXES)), rep
    assert set(rep["axis_stats"]) == {"overall_risk", "auth_strength"}, rep
    for axis, st in rep["axis_stats"].items():
        assert st["count"] == 3, (axis, st)
        for k in ("p50", "p90", "p99"):
            assert 0.0 <= st[k] <= 1.0, (axis, k, st)
    assert rep["escalated_count"] == 3, rep   # unknown axis ignored
    assert rep["generated_at"], rep

    print(format_report(rep))
    print("PASS")
