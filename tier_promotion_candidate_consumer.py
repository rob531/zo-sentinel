"""tier_promotion_candidate_consumer.py -- select MCP servers whose LLM risk
axes say they should be re-scored or re-tiered, for the 3-tier app surface.

Pure scoring module (no HTTP route): the DB rows are read from the REAL app
data layer (app.db / app.models) and handed to the pure functions
``build_candidates`` / ``compute_tier_gap``. No DB writes, no network, no new
verdict tiers -- only the 6 existing ladder verdicts.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

# The 6 existing verdicts, ascending severity. No new tier may be introduced.
TIER_LADDER: List[str] = ["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL", "KNOWN_THREAT"]
TIER_RANK: Dict[str, int] = {t: i for i, t in enumerate(TIER_LADDER)}

# Registry risk_tier vocabulary -> ladder verdict.
_TIER_ALIASES: Dict[str, str] = {
    "TRUSTED_GENERAL": "LOW",
    "TRUSTED_SPECIALIZED": "LOW",
    "TRUSTED_RESEARCH": "LOW",
    "ENTERPRISE_CONTROLLED": "LOW",
    "CAUTION_LIMITED": "MEDIUM",
    "AMBER_UNVERIFIED": "MEDIUM",
    "HIGH_RISK_ISOLATED": "HIGH",
    "KNOWN_THREAT": "CRITICAL",
    "LOW": "LOW",
    "MEDIUM": "MEDIUM",
    "HIGH": "HIGH",
    "CRITICAL": "CRITICAL",
    "UNKNOWN": "UNKNOWN",
}

# Axis label -> implied ladder verdict.
_LABEL_TIER: Dict[str, str] = {
    "CRITICAL": "CRITICAL",
    "KNOWN_THREAT": "KNOWN_THREAT",
    "DANGER": "HIGH",
    "DANGEROUS": "HIGH",
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "MODERATE": "MEDIUM",
    "LOW": "LOW",
    "SAFE": "LOW",
    "NOMINAL": "LOW",
    "MINIMAL": "LOW",
    "WEAK": "HIGH",
    "STRONG": "LOW",
    "BROAD": "MEDIUM",
    "EXTERNAL": "MEDIUM",
    "ESTABLISHED": "LOW",
}

STALE_AFTER_DAYS = 30
_MAX_ROWS = 5000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _norm_tier(value: Any) -> str:
    """Map any current-tier vocabulary onto the 6-verdict ladder."""
    key = str(value or "").strip().upper()
    if key in TIER_RANK:
        return key
    return _TIER_ALIASES.get(key, "UNKNOWN")


def _implied_tier(label: Any, p_top: Optional[float]) -> str:
    """Verdict implied by one axis row (label first, p_top as fallback)."""
    key = str(label or "").strip().upper()
    if key in _LABEL_TIER:
        return _LABEL_TIER[key]
    if p_top is None:
        return "UNKNOWN"
    if p_top >= 0.85:
        return "CRITICAL"
    if p_top >= 0.60:
        return "HIGH"
    if p_top >= 0.35:
        return "MEDIUM"
    if p_top > 0.0:
        return "LOW"
    return "UNKNOWN"


def compute_tier_gap(metadata: dict) -> Tuple[float, dict]:
    """Gap between the server's current tier and the tier its axes suggest.

    metadata keys: current_tier, suggested_tier, axis_name, p_top, scored_at
    (server_id / name / label / p_critical are carried through when present).
    Returns (gap, evidence). gap is the signed ladder distance
    (rank(suggested) - rank(current)); positive means a promotion is implied.
    """
    meta = metadata or {}
    current = _norm_tier(meta.get("current_tier"))
    suggested = _norm_tier(meta.get("suggested_tier"))
    gap = float(TIER_RANK[suggested] - TIER_RANK[current])
    p_top = _as_float(meta.get("p_top"))
    scored = _parse_dt(meta.get("scored_at"))
    evidence = {
        "current_tier": current,
        "suggested_tier": suggested,
        "axis_name": meta.get("axis_name"),
        "p_top": p_top,
        "scored_at": scored.isoformat() if scored else None,
    }
    for extra in ("server_id", "name", "label", "p_critical", "raw_current_tier"):
        if extra in meta and meta[extra] is not None:
            evidence[extra] = meta[extra]
    return gap, evidence


def _stale_days(scored: Optional[datetime], now: datetime) -> Optional[float]:
    if scored is None:
        return None
    return max(0.0, (now - scored).total_seconds() / 86400.0)


def _score_urgency(suggested: str, gap_steps: int, stale_days: Optional[float],
                   p_critical: Optional[float]) -> float:
    base = {"UNKNOWN": 0.0, "LOW": 20.0, "MEDIUM": 40.0, "HIGH": 65.0,
            "CRITICAL": 88.0, "KNOWN_THREAT": 100.0}.get(suggested, 0.0)
    score = base
    score += min(12.0, max(0, gap_steps) * 4.0)
    if stale_days is not None and stale_days > STALE_AFTER_DAYS:
        score += min(10.0, (stale_days - STALE_AFTER_DAYS) / 30.0 * 5.0)
    if p_critical is not None:
        score += min(8.0, p_critical * 8.0)
    return float(max(0.0, min(100.0, score)))


def build_candidates(rows: List[dict], now: Optional[datetime] = None) -> List[dict]:
    """Select the servers whose axis scores warrant a re-score / re-tier.

    rows: dicts from mcp_llm_axis_scores joined to mcp_server_registry with
    keys server_id, name, risk_tier, axis_name, label, p_top, p_critical,
    scored_at. Returns candidates sorted by urgency (desc).
    """
    now = now or _utcnow()
    grouped: Dict[str, List[dict]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        sid = row.get("server_id")
        if not sid:
            continue
        grouped.setdefault(str(sid), []).append(row)

    candidates: List[dict] = []
    for sid, srows in grouped.items():
        name = next((r.get("name") for r in srows if r.get("name")), None)
        raw_tier = next((r.get("risk_tier") for r in srows if r.get("risk_tier")), None)
        current = _norm_tier(raw_tier)

        # Driving axis = the most severe implication across the server's axes.
        driving = None
        driving_tier = "UNKNOWN"
        for r in srows:
            p_top = _as_float(r.get("p_top"))
            implied = _implied_tier(r.get("label"), p_top)
            if (TIER_RANK[implied], p_top or 0.0) > (TIER_RANK[driving_tier],
                                                     (_as_float(driving.get("p_top")) or 0.0)
                                                     if driving else -1.0):
                driving, driving_tier = r, implied
        if driving is None:
            continue

        p_top = _as_float(driving.get("p_top"))
        p_critical = _as_float(driving.get("p_critical"))
        scored = _parse_dt(driving.get("scored_at"))
        axis_name = driving.get("axis_name")
        label = driving.get("label")

        # Trust gate: a verified/established publisher is never published above
        # MEDIUM on the overall_risk axis, so its suggested tier is capped.
        labels = {str(r.get("axis_name")): str(r.get("label") or "").upper()
                  for r in srows if r.get("axis_name")}
        gate = trust_gate(None, name, labels)
        reasons: List[str] = []
        suggested = driving_tier
        if gate.get("trusted"):
            if axis_name == "overall_risk":
                published = gate.get("published_overall_risk") or labels.get("overall_risk")
                capped = _norm_tier(published)
                if TIER_RANK[capped] < TIER_RANK[suggested]:
                    suggested = capped
                    reasons.append(
                        f"trust gate capped suggested tier to {suggested} "
                        f"(basis {gate.get('trust_basis')})")
            else:
                reasons.append(f"trust gate: {gate.get('trust_basis')}")
        if gate.get("masquerade_flag"):
            reasons.append("possible brand impersonation flagged by trust gate")

        stale_days = _stale_days(scored, now)
        is_stale = stale_days is None or stale_days > STALE_AFTER_DAYS
        if stale_days is None:
            reasons.append("no scored_at recorded - needs re-score")
        elif is_stale:
            reasons.append(f"score stale ({stale_days:.0f}d > {STALE_AFTER_DAYS}d) - needs re-score")

        gap, evidence = compute_tier_gap({
            "current_tier": current,
            "suggested_tier": suggested,
            "axis_name": axis_name,
            "p_top": p_top,
            "scored_at": scored.isoformat() if scored else None,
            "server_id": sid,
            "name": name,
            "label": label,
            "p_critical": p_critical,
            "raw_current_tier": raw_tier,
        })
        gap_steps = int(round(abs(gap)))

        if gap > 0:
            reasons.append(
                f"axis {axis_name} label {label or 'n/a'} suggests {suggested} "
                f"(current {current}) - re-tier needed")
        elif is_stale:
            reasons.append(f"tier unchanged ({current}) but re-score is due")
        if p_critical is not None and p_critical >= 0.5:
            reasons.append(f"p_critical {p_critical:.2f}")

        needs_action = bool(gap > 0) or bool(is_stale) or suggested in ("CRITICAL", "KNOWN_THREAT")
        if not needs_action:
            continue

        urgency = _score_urgency(suggested, gap_steps, stale_days, p_critical)
        candidates.append({
            "server_id": sid,
            "name": name,
            "current_tier": current,
            "suggested_tier": suggested,
            "urgency": urgency,
            "reasons": reasons,
            "axis_name": axis_name,
        })

    candidates.sort(key=lambda c: (-c["urgency"], str(c["server_id"])))
    return candidates


def fetch_axis_rows(db: Session, limit: int = _MAX_ROWS) -> List[dict]:
    """Read the axis scores + registry metadata the consumer needs."""
    stmt = (
        select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name,
               McpLlmAxisScore.label, McpLlmAxisScore.p_top,
               McpLlmAxisScore.p_critical, McpLlmAxisScore.scored_at,
               McpServerRegistry.name, McpServerRegistry.risk_tier)
        .select_from(McpLlmAxisScore)
        .join(McpServerRegistry, McpServerRegistry.server_id == McpLlmAxisScore.server_id,
              isouter=True)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(max(1, int(limit)))
    )
    out: List[dict] = []
    for r in db.execute(stmt).all():
        out.append({
            "server_id": r[0], "axis_name": r[1], "label": r[2], "p_top": r[3],
            "p_critical": r[4], "scored_at": r[5], "name": r[6], "risk_tier": r[7],
        })
    return out


def build_candidates_from_session(db: Session = Depends(get_session),
                                  now: Optional[datetime] = None) -> List[dict]:
    """DB-backed entry point: read the real rows, then score them."""
    if not isinstance(db, Session):
        raise TypeError("db must be a SQLAlchemy Session (use Depends(get_session))")
    return build_candidates(fetch_axis_rows(db), now=now)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)

    stale_rows = [{
        "server_id": "srv-stale", "name": "Stale Low MCP", "risk_tier": "LOW",
        "axis_name": "overall_risk", "label": "LOW", "p_top": 0.11,
        "p_critical": 0.01,
        "scored_at": (now - timedelta(days=95)).isoformat(),
    }]
    stale = build_candidates(stale_rows, now=now)
    assert stale, "stale low-p_top server must yield a candidate"
    assert 0.0 <= stale[0]["urgency"] <= 100.0, stale[0]
    assert stale[0]["suggested_tier"] in TIER_LADDER, stale[0]

    crit_rows = [{
        "server_id": "srv-crit", "name": "Critical Data MCP", "risk_tier": "LOW",
        "axis_name": "data_sensitivity", "label": "CRITICAL", "p_top": 0.93,
        "p_critical": 0.88, "scored_at": now.isoformat(),
    }]
    crit = build_candidates(crit_rows, now=now)
    assert crit, "critical-axis server must yield a candidate"
    assert crit[0]["suggested_tier"] == "CRITICAL", crit[0]
    assert crit[0]["urgency"] >= stale[0]["urgency"], (crit[0], stale[0])
    assert 0.0 <= crit[0]["urgency"] <= 100.0, crit[0]

    assert build_candidates([], now=now) == [], "empty input must yield []"

    gap, ev = compute_tier_gap({"current_tier": "LOW", "suggested_tier": "CRITICAL",
                                "axis_name": "data_sensitivity", "p_top": 0.93,
                                "scored_at": now.isoformat()})
    assert gap > 0.0, gap
    for k in ("current_tier", "suggested_tier", "axis_name", "p_top", "scored_at"):
        assert k in ev, ev
    assert ev["suggested_tier"] == "CRITICAL", ev

    # Real data layer: in-memory SQLite via dependency override.
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit",
                            risk_tier="LOW"))
    for _i, (ax, lbl) in enumerate((("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
                                    ("capability_breadth", "BROAD"),
                                    ("data_sensitivity", "CRITICAL"),
                                    ("network_egress", "EXTERNAL"),
                                    ("maintainer_trust", "ESTABLISHED"),
                                    ("exploit_surface", "MODERATE")), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))
    s.commit(); s.close()

    app = FastAPI()

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    with TS() as db:
        rows = fetch_axis_rows(db)
    assert rows, "fixture rows must be readable from the real app models"
    assert rows[0]["server_id"] == "srv1", rows[0]
    live = build_candidates(rows, now=now)
    assert live, "DB rows must produce candidates"
    assert all(x["suggested_tier"] in TIER_LADDER for x in live), live
    print("PASS")
