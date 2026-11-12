"""registry_trust_tier_snapshot_report.py -- point-in-time registry trust health snapshot.

Standalone report generator (no HTTP route): reads the MCP server registry and the
LLM axis scores from the app Postgres via the real SQLAlchemy session
(app.db / app.models) and renders a deterministic Markdown snapshot of
trust-tier health: tier mix, verdict mix, scoring coverage, stale scans and
the riskiest servers.

Pure computation lives in build_registry_trust_tier_snapshot() /
render_registry_trust_tier_snapshot(): stdlib only, no DB writes, no network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

STALE_SCAN_DAYS = 30
TIER_ORDER = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0, "MINIMAL": -1}


# ===================== helpers (stdlib only) =====================
def _parse_ts(value) -> Optional[datetime]:
    """Best-effort ISO-8601 -> timezone-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _percentile(values: List[float], q: float) -> Optional[float]:
    """Linear-interpolation percentile (q in [0,1]) -- no numpy."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _tier_rank(tier: Optional[str]) -> int:
    return TIER_ORDER.get((tier or "").strip().upper(), -1)


# ===================== data access (real app layer) =====================
def load_snapshot_inputs(db: Session) -> Tuple[List[dict], List[dict]]:
    """Registry rows + axis-score rows as plain dicts, in the shape the
    snapshot builder consumes."""
    servers: List[dict] = []
    for r in db.execute(select(McpServerRegistry)).scalars().all():
        servers.append({
            "server_id": r.server_id,
            "name": r.name,
            "url": r.url,
            "risk_tier": r.risk_tier,
            "trust_score": r.trust_score,
            "verdict": r.verdict,
            "last_assessed": r.last_assessed,
            "last_scanned": r.last_scanned,
            "scan_count": r.scan_count,
        })

    axis_scores: List[dict] = []
    for a in db.execute(select(McpLlmAxisScore)).scalars().all():
        axis_scores.append({
            "server_id": a.server_id,
            "axis_name": a.axis_name,
            "p_top": a.p_top,
            "scored_at": a.scored_at,
            "label": a.label,
        })
    return servers, axis_scores


def build_snapshot_from_db(db: Session, as_of: Optional[str] = None) -> dict:
    """Read the real tables and build the snapshot dict."""
    servers, axis_scores = load_snapshot_inputs(db)
    stamp = as_of or datetime.now(timezone.utc).isoformat()
    return build_registry_trust_tier_snapshot(servers, axis_scores, stamp)


# ===================== report computation =====================
def build_registry_trust_tier_snapshot(servers: List[dict], axis_scores: List[dict],
                                       as_of: str) -> dict:
    """Point-in-time snapshot of registry trust health. Deterministic, pure."""
    as_of_dt = _parse_ts(as_of) or datetime.now(timezone.utc)
    cutoff = as_of_dt - timedelta(days=STALE_SCAN_DAYS)

    # --- per-server gating (official publishers must not read as false HIGH) ---
    labels_by_server: Dict[str, Dict[str, str]] = {}
    for a in axis_scores:
        sid = a.get("server_id")
        axis = a.get("axis_name")
        label = a.get("label")
        if sid and axis and label:
            labels_by_server.setdefault(sid, {})[axis] = str(label).upper()

    gated: List[dict] = []
    for s in servers:
        sid = s.get("server_id")
        gate = trust_gate(s.get("url"), s.get("name"), labels_by_server.get(sid, {}))
        published = gate.get("published_overall_risk") or (s.get("risk_tier") or "UNSET")
        gated.append({
            "server_id": sid,
            "name": s.get("name"),
            "risk_tier": (s.get("risk_tier") or "UNSET"),
            "published_risk_tier": str(published).upper(),
            "trust_score": s.get("trust_score"),
            "verdict": s.get("verdict"),
            "last_scanned": s.get("last_scanned"),
            "trusted": bool(gate.get("trusted")),
            "masquerade_flag": bool(gate.get("masquerade_flag")),
        })

    total = len(gated)

    tier_counts: Dict[str, int] = {}
    for g in gated:
        tier_counts[g["published_risk_tier"]] = tier_counts.get(g["published_risk_tier"], 0) + 1
    tier_pct = {t: (round(100.0 * c / total, 2) if total else 0.0)
                for t, c in tier_counts.items()}

    verdict_counts: Dict[str, int] = {}
    for g in gated:
        v = (g["verdict"] or "unreviewed").strip().lower()
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    scored_ids = {a.get("server_id") for a in axis_scores if a.get("server_id")}
    unscored = [g for g in gated if g["server_id"] not in scored_ids]

    stale_ids: List[str] = []
    for g in gated:
        scanned = _parse_ts(g["last_scanned"])
        if scanned is None or scanned < cutoff:
            stale_ids.append(g["server_id"])

    # --- axis coverage ---
    by_axis: Dict[str, Dict[str, List[float]]] = {}
    axis_present: Dict[str, set] = {}
    for a in axis_scores:
        axis = a.get("axis_name")
        if not axis:
            continue
        axis_present.setdefault(axis, set()).add(a.get("server_id"))
        p = a.get("p_top")
        if p is None:
            continue
        try:
            by_axis.setdefault(axis, {"p": []})["p"].append(float(p))
        except (TypeError, ValueError):
            continue

    axis_coverage: Dict[str, dict] = {}
    for axis in sorted(set(list(by_axis.keys()) + list(axis_present.keys()))):
        vals = by_axis.get(axis, {}).get("p", [])
        med = _median(vals)
        p90 = _percentile(vals, 0.90)
        axis_coverage[axis] = {
            "present": len(axis_present.get(axis, ())),
            "median_p_top": round(med, 6) if med is not None else None,
            "p90_p_top": round(p90, 6) if p90 is not None else None,
        }

    def _trust_key(g: dict):
        ts = g.get("trust_score")
        ts = float(ts) if isinstance(ts, (int, float)) else 0.0
        return (-_tier_rank(g["published_risk_tier"]), ts, str(g["server_id"]))

    riskiest = sorted(gated, key=_trust_key)[:10]
    top_10 = [{"server_id": g["server_id"], "name": g["name"],
               "risk_tier": g["published_risk_tier"], "trust_score": g["trust_score"]}
              for g in riskiest]

    return {
        "as_of": as_of,
        "total_servers": total,
        "tier_counts": tier_counts,
        "tier_pct": tier_pct,
        "verdict_counts": verdict_counts,
        "unscored_servers": len(unscored),
        "unscored_server_ids": [g["server_id"] for g in unscored],
        "stale_scans": len(stale_ids),
        "stale_scan_server_ids": stale_ids,
        "axis_coverage": axis_coverage,
        "top_10_riskiest": top_10,
    }


# ===================== markdown rendering =====================
def _table(headers: Iterable[str], rows: Iterable[Iterable]) -> str:
    headers = list(headers)
    lines = ["| " + " | ".join(str(h) for h in headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join("" if c is None else str(c) for c in row) + " |")
    return "\n".join(lines)


def render_registry_trust_tier_snapshot(report: dict) -> str:
    """Markdown rendering of the snapshot dict."""
    out: List[str] = []
    out.append("# Registry Trust Tier Snapshot")
    out.append("")
    out.append(f"- as_of: {report.get('as_of')}")
    out.append(f"- total_servers: {report.get('total_servers')}")
    out.append(f"- unscored_servers: {report.get('unscored_servers')}")
    out.append(f"- stale_scans (older than {STALE_SCAN_DAYS}d): {report.get('stale_scans')}")
    out.append("")

    out.append("## Tier counts")
    out.append("")
    tier_counts = report.get("tier_counts") or {}
    tier_pct = report.get("tier_pct") or {}
    out.append(_table(["risk tier", "count", "pct"],
                      [(t, tier_counts.get(t, 0), tier_pct.get(t, 0.0))
                       for t in sorted(tier_counts, key=lambda x: (-_tier_rank(x), x))]))
    out.append("")

    out.append("## Verdict counts")
    out.append("")
    verdict_counts = report.get("verdict_counts") or {}
    out.append(_table(["verdict", "count"],
                      [(v, verdict_counts[v]) for v in sorted(verdict_counts)]))
    out.append("")

    out.append("## Axis coverage")
    out.append("")
    coverage = report.get("axis_coverage") or {}
    out.append(_table(["axis", "present", "median p_top", "p90 p_top"],
                      [(axis, coverage[axis].get("present"),
                        coverage[axis].get("median_p_top"),
                        coverage[axis].get("p90_p_top"))
                       for axis in sorted(coverage)]))
    out.append("")

    out.append("## Top 10 riskiest servers")
    out.append("")
    out.append(_table(["server_id", "name", "risk tier", "trust score"],
                      [(r.get("server_id"), r.get("name"), r.get("risk_tier"),
                        r.get("trust_score")) for r in (report.get("top_10_riskiest") or [])]))
    out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    AS_OF = "2026-11-12T00:00:00+00:00"
    now = datetime.now(timezone.utc)
    fresh = now - timedelta(days=2)
    old = now - timedelta(days=90)

    s = TS()
    s.add(McpServerRegistry(server_id="srv_high", name="Risky MCP",
                            url="https://github.com/evilcorp/mcp",
                            risk_tier="HIGH", trust_score=0.31, verdict="reviewed",
                            last_scanned=fresh, scan_count=4))
    s.add(McpServerRegistry(server_id="srv_low", name="Calm MCP",
                            url="https://github.com/stripe/agent-toolkit",
                            risk_tier="LOW", trust_score=0.88, verdict="reviewed",
                            last_scanned=old, scan_count=2))
    s.add(McpServerRegistry(server_id="srv_none", name="Unscored MCP",
                            url="https://github.com/newco/mcp",
                            risk_tier="MEDIUM", trust_score=0.55, verdict="unreviewed",
                            last_scanned=fresh, scan_count=0))
    s.add(McpLlmAxisScore(id=1, server_id="srv_high", axis_name="overall_risk",
                          label="HIGH", model_version="v3.0_40974559", p_top=0.9))
    s.add(McpLlmAxisScore(id=2, server_id="srv_high", axis_name="auth_strength",
                          label="WEAK", model_version="v3.0_40974559", p_top=0.2))
    s.add(McpLlmAxisScore(id=3, server_id="srv_low", axis_name="auth_strength",
                          label="STRONG", model_version="v3.0_40974559", p_top=0.1))
    s.commit()
    s.close()

    app = FastAPI()

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    db = next(app.dependency_overrides[get_session]())

    servers, axis_scores = load_snapshot_inputs(db)
    assert len(servers) == 3, servers
    assert len(axis_scores) == 3, axis_scores

    report = build_registry_trust_tier_snapshot(servers, axis_scores, AS_OF)
    assert report["total_servers"] == 3, report
    assert sum(report["tier_counts"].values()) == 3, report
    assert report["unscored_servers"] == 1, report
    assert report["unscored_server_ids"] == ["srv_none"], report
    assert report["stale_scans"] == 1, report
    assert report["verdict_counts"]["reviewed"] == 2, report
    assert report["axis_coverage"]["overall_risk"]["present"] == 1, report
    assert report["axis_coverage"]["overall_risk"]["median_p_top"] == 0.9, report
    assert report["axis_coverage"]["auth_strength"]["present"] == 2, report
    assert report["axis_coverage"]["auth_strength"]["median_p_top"] == 0.15, report
    assert report["top_10_riskiest"][0]["server_id"] == "srv_high", report

    md = render_registry_trust_tier_snapshot(report)
    assert "tier" in md.lower(), md
    assert "## Axis coverage" in md, md

    # DB-backed build must agree with the pure build.
    db_report = build_snapshot_from_db(db, AS_OF)
    assert db_report["tier_counts"] == report["tier_counts"], db_report
    assert db_report["unscored_servers"] == 1, db_report

    # Edge case: empty inputs must not explode.
    empty = build_registry_trust_tier_snapshot([], [], AS_OF)
    assert empty["total_servers"] == 0 and empty["tier_counts"] == {}, empty
    assert empty["unscored_servers"] == 0 and empty["stale_scans"] == 0, empty
    assert render_registry_trust_tier_snapshot(empty).strip(), "empty render"

    print("PASS")
