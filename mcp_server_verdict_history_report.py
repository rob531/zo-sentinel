# deps: sqlalchemy
"""mcp_server_verdict_history_report.py -- per-server verdict / risk-tier history report.

Reads the SFT model's own scores (mcp_llm_axis_scores) plus registry metadata
(mcp_server_registry) through the REAL app data layer (app.db / app.models) and
produces a non-HTTP report artefact:

    build_verdict_history(server_id, days=30) -> dict
    render_verdict_history(history) -> str
    main(argv) -> int

Read-only: no network, no writes, no duckdb.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

_REPO_ROOT = "/home/workspace/zo_sentinel"
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_session  # noqa: F401  (get_session = real app seam)
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _tier_rank(tier: Optional[str]) -> int:
    return RISK_ORDER.get((tier or "").strip().upper(), -1)


def _published_tier(reg: Optional[McpServerRegistry], labels: Dict[str, Optional[str]]) -> str:
    """Published (post trust-gate) overall tier for one scoring event."""
    url = reg.url if reg is not None else None
    name = reg.name if reg is not None else None
    gate = trust_gate(url, name, {k: v for k, v in labels.items() if v})
    tier = gate.get("published_overall_risk") or labels.get("overall_risk")
    if not tier and reg is not None:
        tier = reg.risk_tier
    return (tier or "UNKNOWN").strip().upper()


def build_verdict_history(server_id: str, days: int = 30,
                           db: Optional[Session] = None) -> dict:
    """Verdict / risk-tier history for one server over the last `days` days.

    Returns {server_id, days, points: [{scored_at, overall_p_top, risk_tier,
    escalated_axes}], summary: {tier_changes, worst_tier, latest_tier}}.
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        days = max(1, int(days))
        cutoff = datetime.utcnow() - timedelta(days=days)
        rows = db.execute(
            select(McpLlmAxisScore)
            .where(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.axis_name.in_(AXES),
                McpLlmAxisScore.scored_at.isnot(None),
                McpLlmAxisScore.scored_at >= cutoff,
            )
            .order_by(McpLlmAxisScore.scored_at.asc())
        ).scalars().all()

        reg = db.get(McpServerRegistry, server_id)

        groups: Dict[str, List[McpLlmAxisScore]] = {}
        for r in rows:
            groups.setdefault(r.scored_at.isoformat(), []).append(r)

        points: List[dict] = []
        for key in sorted(groups):
            group = groups[key]
            labels = {r.axis_name: r.label for r in group}
            overall = next((r for r in group if r.axis_name == "overall_risk"), None)
            points.append({
                "scored_at": key,
                "overall_p_top": float(overall.p_top) if (overall is not None
                                                          and overall.p_top is not None) else None,
                "risk_tier": _published_tier(reg, labels),
                "escalated_axes": sorted({r.axis_name for r in group if r.escalated}),
            })

        tiers = [p["risk_tier"] for p in points]
        tier_changes = sum(1 for a, b in zip(tiers, tiers[1:]) if a != b)
        worst = None
        for t in tiers:
            if _tier_rank(t) > _tier_rank(worst):
                worst = t
        return {
            "server_id": server_id,
            "days": days,
            "points": points,
            "summary": {
                "tier_changes": tier_changes,
                "worst_tier": worst,
                "latest_tier": tiers[-1] if tiers else None,
            },
        }
    finally:
        if own_session:
            db.close()


def render_verdict_history(history: dict) -> str:
    """Plain-text table of the history artefact, oldest first."""
    history = history or {}
    points = sorted(history.get("points") or [], key=lambda p: str(p.get("scored_at")))
    summary = history.get("summary") or {}
    lines = [
        f"Verdict history for server_id={history.get('server_id')} (last {history.get('days')} days)",
        "",
        f"{'scored_at':<32} {'risk_tier':<10} {'p_top':>8}  escalated_axes",
        "-" * 78,
    ]
    if not points:
        lines.append("(no scoring events in window)")
    for p in points:
        ptop = p.get("overall_p_top")
        ptop_s = "" if ptop is None else f"{float(ptop):.4f}"
        esc = ",".join(p.get("escalated_axes") or []) or "-"
        lines.append(f"{str(p.get('scored_at')):<32} {str(p.get('risk_tier')):<10} "
                     f"{ptop_s:>8}  {esc}")
    lines += [
        "",
        f"tier_changes={summary.get('tier_changes')} "
        f"worst_tier={summary.get('worst_tier')} "
        f"latest_tier={summary.get('latest_tier')}",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None, db: Optional[Session] = None) -> int:
    """CLI entrypoint: report the verdict history for one server."""
    argv = list(sys.argv[1:] if argv is None else argv)
    server_id = ""
    days = 30
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--server-id", "-s") and i + 1 < len(argv):
            server_id = argv[i + 1]
            i += 2
        elif a in ("--days", "-d") and i + 1 < len(argv):
            try:
                days = int(argv[i + 1])
            except ValueError:
                print("days must be an integer", file=sys.stderr)
                return 2
            i += 2
        elif not a.startswith("-") and not server_id:
            server_id = a
            i += 1
        else:
            i += 1
    if not server_id:
        print("usage: mcp_server_verdict_history_report.py --server-id <id> [--days N]",
              file=sys.stderr)
        return 2
    try:
        history = build_verdict_history(server_id, days=days, db=db)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(render_verdict_history(history))
    return 0


if __name__ == "__main__":  # self-test: real app imports, SQLite via dependency override
    from datetime import datetime as _dt

    from sqlalchemy import create_engine, update
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()

    s.add(McpServerRegistry(server_id="srv_a", name="Acme MCP",
                            url="https://github.com/acme/mcp", risk_tier="HIGH"))
    s.add(McpServerRegistry(server_id="srv_b", name="Beta MCP",
                            url="https://github.com/beta/mcp", risk_tier="MEDIUM"))

    now = _dt.utcnow()
    day1 = now - timedelta(days=2)
    day2 = now - timedelta(days=1)

    rows = []
    mid = 0
    for sid, mv, label, day, ptop in (
        ("srv_a", "v3.0_40974559", "HIGH", day1, 0.91),
        ("srv_a", "v3.1_40974560", "MEDIUM", day2, 0.42),
        ("srv_b", "v3.0_40974559", "MEDIUM", day1, 0.55),
        ("srv_b", "v3.1_40974560", "MEDIUM", day2, 0.51),
    ):
        mid += 1
        rows.append((mid, sid, "overall_risk", label, mv, day, ptop))
        mid += 1
        rows.append((mid, sid, "maintainer_trust", "UNKNOWN", mv, day, ptop))
    for (rid, sid, axis, label, mv, day, ptop) in rows:
        s.add(McpLlmAxisScore(id=rid, server_id=sid, axis_name=axis, label=label,
                              model_version=mv))
    s.commit()

    # scored_at / p_top / escalated are set post-insert (DB defaults otherwise).
    for (rid, sid, axis, label, mv, day, ptop) in rows:
        s.execute(update(McpLlmAxisScore).where(McpLlmAxisScore.id == rid)
                  .values(scored_at=day, p_top=ptop, escalated=False))
    mid += 1
    s.add(McpLlmAxisScore(id=mid, server_id="srv_a", axis_name="data_sensitivity",
                          label="SENSITIVE", model_version="v3.1_40974560"))
    s.commit()
    s.execute(update(McpLlmAxisScore).where(McpLlmAxisScore.id == mid)
              .values(scored_at=day2, p_top=0.33, escalated=True))
    s.commit()

    hist = build_verdict_history("srv_a", days=30, db=s)
    assert hist["server_id"] == "srv_a", hist
    assert hist["days"] == 30, hist
    assert len(hist["points"]) == 2, hist
    stamps = [p["scored_at"] for p in hist["points"]]
    assert stamps == sorted(stamps), stamps
    assert hist["points"][0]["risk_tier"] == "HIGH", hist
    assert hist["points"][1]["risk_tier"] == "MEDIUM", hist
    assert hist["points"][1]["escalated_axes"] == ["data_sensitivity"], hist
    assert hist["summary"]["tier_changes"] == 1, hist
    assert hist["summary"]["worst_tier"] == "HIGH", hist
    assert hist["summary"]["latest_tier"] == "MEDIUM", hist

    flat = build_verdict_history("srv_b", days=30, db=s)
    assert flat["summary"]["tier_changes"] == 0, flat

    out = render_verdict_history(hist)
    assert "srv_a" in out, out
    assert "HIGH" in out and "MEDIUM" in out, out

    empty = build_verdict_history("nope", days=30, db=s)
    assert empty["points"] == [], empty
    assert empty["summary"]["tier_changes"] == 0, empty
    assert "nope" in render_verdict_history(empty)

    assert main(["--server-id", "srv_a", "--days", "30"], db=s) == 0
    assert main([], db=s) == 2

    s.close()
    print("PASS")
