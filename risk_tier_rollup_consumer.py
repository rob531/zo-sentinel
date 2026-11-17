"""risk_tier_rollup_consumer.py -- canonical risk-tier derivation from LLM axis scores.

A scoring-consumer LIBRARY: turns mcp_llm_axis_scores rows into a per-server risk
tier and a population rollup, so dashboards and report generators read ONE canonical
tier derivation instead of re-implementing it.

Data layer is the REAL app data layer (app.db / app.models). The loader reads ONLY
mcp_llm_axis_scores. This file NEVER writes to the DB and NEVER opens a network
connection -- the tier rule is a pure function over dict rows.

Tier rule (canonical):
  * overall_risk axis p_top >= high_threshold            -> HIGH (driver: overall_risk)
  * any axis p_critical >= critical_threshold             -> HIGH (driver: that axis)
  * else p_top >= 0.4                                    -> MEDIUM
  * else                                                 -> LOW
  * missing / empty axis set                             -> UNKNOWN
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

TIERS = ("HIGH", "MEDIUM", "LOW", "UNKNOWN")
MEDIUM_P_TOP = 0.4

SCORE_COLUMNS = (
    McpLlmAxisScore.server_id,
    McpLlmAxisScore.axis_name,
    McpLlmAxisScore.label,
    McpLlmAxisScore.label_index,
    McpLlmAxisScore.p_top,
    McpLlmAxisScore.p_critical,
    McpLlmAxisScore.p_danger,
    McpLlmAxisScore.escalated,
    McpLlmAxisScore.escalated_to,
    McpLlmAxisScore.decision_rule_version,
    McpLlmAxisScore.model_version,
    McpLlmAxisScore.scored_at,
)


def db_session_dependency():
    """FastAPI-compatible dependency exposing the REAL app session.

    Consumers mount this so their endpoints read the app Postgres through the
    single seam every other feature router uses.
    """
    yield from get_session()


def _num(row: Dict, key: str) -> float:
    value = row.get(key)
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _axis_rows(axis_rows: Optional[List[Dict]]) -> List[Dict]:
    if not axis_rows:
        return []
    return [r for r in axis_rows if isinstance(r, dict)]


def tier_for_axis_scores(axis_rows: List[Dict], *, critical_threshold: float = 0.5,
                         high_threshold: float = 0.75) -> dict:
    """Derive ONE canonical risk tier for a server from its axis-score rows."""
    rows = _axis_rows(axis_rows)
    if not rows:
        return {"server_id": "", "risk_tier": "UNKNOWN", "driver_axis": "",
                "driver_p_top": 0.0, "escalated": False, "axis_count": 0}

    server_id = str(rows[0].get("server_id") or "")
    escalated = any(bool(r.get("escalated")) for r in rows)
    by_axis: Dict[str, Dict] = {}
    for r in rows:
        by_axis[str(r.get("axis_name") or "")] = r

    def _result(tier: str, driver_axis: str, driver_p_top: float) -> dict:
        return {"server_id": server_id, "risk_tier": tier, "driver_axis": driver_axis,
                "driver_p_top": driver_p_top, "escalated": escalated,
                "axis_count": len(by_axis)}

    # 1) overall_risk p_top >= high_threshold -> HIGH
    overall = by_axis.get("overall_risk")
    if overall is not None:
        p_top = _num(overall, "p_top")
        if p_top >= high_threshold:
            return _result("HIGH", "overall_risk", p_top)

    # 2) any axis p_critical >= critical_threshold -> HIGH (driver = that axis)
    critical_axis = ""
    critical_p = 0.0
    for r in rows:
        p_crit = _num(r, "p_critical")
        if p_crit >= critical_threshold and p_crit > critical_p:
            critical_p = p_crit
            critical_axis = str(r.get("axis_name") or "")
    if critical_axis:
        return _result("HIGH", critical_axis, _num(by_axis.get(critical_axis, {}), "p_top"))

    # 3/4) p_top ladder -> MEDIUM / LOW (driver = the strongest axis)
    driver_axis = ""
    driver_p_top = 0.0
    for r in rows:
        p_top = _num(r, "p_top")
        if p_top > driver_p_top:
            driver_p_top = p_top
            driver_axis = str(r.get("axis_name") or "")
    if overall is not None and _num(overall, "p_top") >= driver_p_top:
        driver_axis = "overall_risk"
        driver_p_top = _num(overall, "p_top")

    tier = "MEDIUM" if driver_p_top >= MEDIUM_P_TOP else "LOW"
    return _result(tier, driver_axis, driver_p_top)


def rollup_by_tier(axis_rows: List[Dict]) -> dict:
    """Population rollup: tier counts, server count, and per-axis tier counts."""
    groups: Dict[str, List[Dict]] = {}
    for r in _axis_rows(axis_rows):
        groups.setdefault(str(r.get("server_id") or ""), []).append(r)

    counts: Dict[str, int] = {tier: 0 for tier in TIERS}
    by_axis: Dict[str, Dict[str, int]] = {}
    for rows in groups.values():
        tier = tier_for_axis_scores(rows)["risk_tier"]
        counts[tier] = counts.get(tier, 0) + 1
        for axis in {str(r.get("axis_name") or "") for r in rows}:
            bucket = by_axis.setdefault(axis, {t: 0 for t in TIERS})
            bucket[tier] = bucket.get(tier, 0) + 1

    return {"counts": counts, "servers": len(groups), "by_axis": by_axis}


def load_axis_scores(app_db_session: Session,
                     server_ids: Optional[List[str]] = None) -> List[Dict]:
    """Load mcp_llm_axis_scores rows as plain dicts (read-only, no writes)."""
    stmt = select(*SCORE_COLUMNS).order_by(McpLlmAxisScore.server_id,
                                            McpLlmAxisScore.axis_name)
    if server_ids:
        stmt = stmt.where(McpLlmAxisScore.server_id.in_(list(server_ids)))
    result = app_db_session.execute(stmt)
    return [dict(row._mapping) for row in result.all()]


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                          label="HIGH", model_version="v3.0_40974559",
                          p_top=0.9, p_critical=0.0))
    s.add(McpLlmAxisScore(id=2, server_id="srv1", axis_name="auth_strength",
                          label="WEAK", model_version="v3.0_40974559",
                          p_top=0.2, p_critical=0.0))
    s.add(McpLlmAxisScore(id=3, server_id="srv2", axis_name="auth_strength",
                          label="WEAK", model_version="v3.0_40974559",
                          p_top=0.1, p_critical=0.8))
    s.commit()
    s.close()

    # (a) overall_risk p_top 0.9 -> HIGH
    rows_a = [{"server_id": "srv1", "axis_name": "overall_risk", "p_top": 0.9,
               "p_critical": 0.0, "escalated": False}]
    ta = tier_for_axis_scores(rows_a)
    assert ta["risk_tier"] == "HIGH", ta
    assert ta["driver_axis"] == "overall_risk", ta

    # (b) auth_strength p_critical 0.8 -> HIGH, driver auth_strength
    rows_b = [{"server_id": "srv2", "axis_name": "overall_risk", "p_top": 0.1,
               "p_critical": 0.0, "escalated": False},
              {"server_id": "srv2", "axis_name": "auth_strength", "p_top": 0.1,
               "p_critical": 0.8, "escalated": True}]
    tb = tier_for_axis_scores(rows_b)
    assert tb["risk_tier"] == "HIGH", tb
    assert tb["driver_axis"] == "auth_strength", tb
    assert tb["escalated"] is True, tb

    # (c) all-low row set -> LOW
    rows_c = [{"server_id": "srv3", "axis_name": "overall_risk", "p_top": 0.05,
               "p_critical": 0.0, "escalated": False},
              {"server_id": "srv3", "axis_name": "exploit_surface", "p_top": 0.1,
               "p_critical": 0.0, "escalated": False}]
    tc = tier_for_axis_scores(rows_c)
    assert tc["risk_tier"] == "LOW", tc

    # (d) empty row set -> UNKNOWN
    td = tier_for_axis_scores([])
    assert td["risk_tier"] == "UNKNOWN", td
    assert td["axis_count"] == 0, td

    # (e) rollup counts sum to the number of servers
    roll = rollup_by_tier(rows_a + rows_b + rows_c)
    assert roll["servers"] == 3, roll
    assert sum(roll["counts"].values()) == roll["servers"], roll
    assert roll["counts"]["HIGH"] == 2, roll
    assert roll["counts"]["LOW"] == 1, roll
    assert roll["by_axis"]["auth_strength"]["HIGH"] == 1, roll

    # loader reads the real table through the app session
    db = TS()
    loaded = load_axis_scores(db, ["srv1"])
    assert len(loaded) == 2, loaded
    assert loaded[0]["server_id"] == "srv1", loaded
    assert loaded[0]["model_version"] == "v3.0_40974559", loaded
    assert tier_for_axis_scores(loaded)["risk_tier"] == "HIGH", loaded
    db.close()

    # the dependency override seam mirrors the exemplar's FastAPI self-test
    app = FastAPI()

    @app.get("/rollup")
    def _rollup(db=Depends(get_session)):
        return rollup_by_tier(load_axis_scores(db))

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app)
    r = client.get("/rollup")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["servers"] == 2, body
    assert body["counts"]["HIGH"] == 2, body
    print("PASS")
