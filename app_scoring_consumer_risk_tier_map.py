"""app_scoring_consumer_risk_tier_map.py -- canonical 3-tier risk_tier mapping.

Scoring consumers read the SFT model's stored axis scores (mcp_llm_axis_scores)
and need ONE canonical tier vocabulary: HIGH_RISK / CAUTION / LOW / UNKNOWN.

This module is pure mapping logic over the stored axis scores:
  - axis_label_map(scored_at)  -> {axis_name: label} for the newest scored axis set
  - risk_tier_from_axes(axes)  -> canonical tier from {axis_name: p_top}
  - tier_for_server(sid, axes) -> canonical tier, trust-capped for verified publishers

No HTTP route, no DB writes, no network, no import-time side effects. All DB
reads go through the REAL app data layer (app.db.get_session / app.models).
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, Iterator, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

CANONICAL_TIERS: tuple[str, ...] = ("HIGH_RISK", "CAUTION", "LOW", "UNKNOWN")

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

HIGH_RISK_OVERALL = 0.80
HIGH_RISK_AXIS = 0.85
CAUTION_OVERALL = 0.50
CAUTION_AXIS = 0.60

# Consumer/test override: a callable returning a Session. None -> app.db.get_session.
SESSION_PROVIDER = None


# ===================== session helpers (real app data layer) =====================

def _open_session() -> Session:
    if SESSION_PROVIDER is not None:
        return SESSION_PROVIDER()
    return next(get_session())


@contextmanager
def _session_ctx(db: Optional[Session] = None) -> Iterator[Session]:
    if db is not None:
        yield db
        return
    gen = get_session() if SESSION_PROVIDER is None else None
    s = _open_session()
    try:
        yield s
    finally:
        if gen is not None:
            try:
                next(gen)
            except StopIteration:
                pass
        else:
            s.close()


# ===================== pure mapping logic =====================

def _num(value: object) -> Optional[float]:
    """Coerce a stored p_top into a finite float, or None when unusable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def risk_tier_from_axes(axes: Dict[str, float]) -> str:
    """Map {axis_name: p_top} to a canonical tier.

    UNKNOWN  : no usable p_top at all
    HIGH_RISK: overall_risk >= 0.80, or any single non-overall axis >= 0.85
    CAUTION  : overall_risk >= 0.50, or any single axis >= 0.60
    LOW      : otherwise
    """
    if not axes:
        return "UNKNOWN"

    usable: Dict[str, float] = {}
    for name, raw in axes.items():
        f = _num(raw)
        if f is not None:
            usable[name] = f
    if not usable:
        return "UNKNOWN"

    overall = usable.get("overall_risk")
    others = [v for k, v in usable.items() if k != "overall_risk"]
    every = list(usable.values())

    if overall is not None and overall >= HIGH_RISK_OVERALL:
        return "HIGH_RISK"
    if any(v >= HIGH_RISK_AXIS for v in others):
        return "HIGH_RISK"
    if overall is not None and overall >= CAUTION_OVERALL:
        return "CAUTION"
    if any(v >= CAUTION_AXIS for v in every):
        return "CAUTION"
    return "LOW"


def _label_for_p_top(p_top: Optional[float]) -> str:
    """Model-label vocabulary used by trust_gate (RISK_ORDER keys)."""
    f = _num(p_top)
    if f is None:
        return "LOW"
    if f >= HIGH_RISK_OVERALL:
        return "HIGH"
    if f >= CAUTION_OVERALL:
        return "MEDIUM"
    return "LOW"


def tier_for_server(server_id: str, axes: Dict[str, float],
                    db: Optional[Session] = None) -> str:
    """Canonical tier for a server, trust-capped for verified/established publishers.

    The cap mirrors verdict_breakdown_api: an official publisher is never shown as
    false HIGH_RISK, so HIGH_RISK is published as CAUTION.
    """
    tier = risk_tier_from_axes(axes)
    if tier != "HIGH_RISK":
        return tier

    try:
        with _session_ctx(db) as s:
            reg = s.get(McpServerRegistry, server_id)
            url = reg.url if reg else None
            name = reg.name if reg else None
    except Exception:
        return tier

    labels = {ax: _label_for_p_top(axes.get(ax)) for ax in axes}
    try:
        gate = trust_gate(url, name, labels)
    except Exception:
        return tier
    if gate.get("trusted") or gate.get("masquerade_flag"):
        return "CAUTION"
    return tier


def axis_label_map(scored_at: Optional[datetime] = None,
                   db: Optional[Session] = None) -> Dict[str, str]:
    """{axis_name: label} for the newest stored axis set (optionally <= scored_at)."""
    with _session_ctx(db) as s:
        stmt = select(McpLlmAxisScore)
        if scored_at is not None:
            stmt = stmt.where(McpLlmAxisScore.scored_at.isnot(None),
                              McpLlmAxisScore.scored_at <= scored_at)
        stmt = stmt.order_by(McpLlmAxisScore.scored_at.desc(),
                             McpLlmAxisScore.id.desc()).limit(1)
        newest = s.execute(stmt).scalars().first()
        if newest is None:
            return {}

        rows = s.execute(
            select(McpLlmAxisScore).where(
                McpLlmAxisScore.server_id == newest.server_id,
                McpLlmAxisScore.model_version == newest.model_version,
            )
        ).scalars().all()

    out: Dict[str, str] = {}
    for r in rows:
        if r.axis_name and r.label:
            out[r.axis_name] = r.label
    return out


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from datetime import datetime as _dt

    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit",
                            registry_source="user_submission", verdict="unreviewed"))
    s.add(McpServerRegistry(server_id="srv3", name="Unknown MCP",
                            url="https://example.invalid/some-mcp",
                            registry_source="user_submission", verdict="unreviewed"))
    old = _dt(2026, 1, 1, 0, 0, 0)
    for _i, (ax, lbl) in enumerate((("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
                    ("capability_breadth", "BROAD"), ("data_sensitivity", "CRITICAL"),
                    ("network_egress", "EXTERNAL"), ("maintainer_trust", "ESTABLISHED"),
                    ("exploit_surface", "MODERATE")), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559", scored_at=old))
    for _i, (ax, lbl) in enumerate((("overall_risk", "MEDIUM"), ("auth_strength", "STRONG"),
                    ("capability_breadth", "BROAD"), ("data_sensitivity", "MODERATE"),
                    ("network_egress", "INTERNAL"), ("maintainer_trust", "UNKNOWN"),
                    ("exploit_surface", "LOW")), start=100):
        s.add(McpLlmAxisScore(id=_i, server_id="srv3", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))
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

    import app_scoring_consumer_risk_tier_map as mod
    mod.SESSION_PROVIDER = TS

    # --- pure tier mapping ---
    assert mod.risk_tier_from_axes({}) == "UNKNOWN", "empty axes -> UNKNOWN"
    assert mod.risk_tier_from_axes({"overall_risk": 0.9}) == "HIGH_RISK", "overall >= 0.80"
    assert mod.risk_tier_from_axes({"auth_strength": 0.9, "overall_risk": 0.1}) == "HIGH_RISK", \
        "critical-axis override"
    assert mod.risk_tier_from_axes({"overall_risk": 0.55}) == "CAUTION", "overall >= 0.50"
    assert mod.risk_tier_from_axes({"overall_risk": 0.05}) == "LOW", "low"
    assert mod.risk_tier_from_axes({"overall_risk": None}) == "UNKNOWN", "unusable p_top"
    assert mod.risk_tier_from_axes({"exploit_surface": 0.65}) == "CAUTION", "axis >= 0.60"
    assert mod.CANONICAL_TIERS == ("HIGH_RISK", "CAUTION", "LOW", "UNKNOWN"), "tier vocabulary"

    # --- newest stored axis label set ---
    labels = mod.axis_label_map() or {}
    assert set(labels) <= set(mod.AXES), labels
    assert labels.get("overall_risk") == "MEDIUM", labels   # srv3 is the newest set
    assert set(mod.axis_label_map(scored_at=old) or {}) <= set(mod.AXES), "scored_at filter"

    # --- per-server tier with trust capping ---
    assert mod.tier_for_server("srv1", {"overall_risk": 0.9}) == "CAUTION", \
        "verified publisher is not published as HIGH_RISK"
    assert mod.tier_for_server("srv3", {"overall_risk": 0.9}) == "HIGH_RISK", \
        "unverified publisher keeps HIGH_RISK"
    assert mod.tier_for_server("srv3", {"overall_risk": 0.05}) == "LOW", "low tier"

    print("PASS")
