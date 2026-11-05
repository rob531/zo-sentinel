"""registry_verdict_mix_report.py -- verdict / risk-tier distribution report for the
MCP registry, backed by the REAL app data layer (app.db / app.models).

Purpose: the dashboard needs a verified distribution source for the 3-tier app
surface ("how many servers are TRUSTED_GENERAL vs CAUTION_LIMITED ..."). This module
summarises the mix of ALREADY-PUBLISHED verdicts stored on mcp_server_registry.

Interface:
    build_report(rows: list[dict] | None = None) -> dict
        rows: pre-fetched registry rows, each
              {server_id, verdict, risk_tier, registry_source, last_assessed, confidence}
        rows=None -> read mcp_server_registry through the app DB session
                    (app/db.py get_session) when no session is supplied.
        returns a JSON-serialisable dict:
            {generated_at, total, by_verdict, by_risk_tier, by_source,
             confidence: {min, max, avg, n}, empty_slices}

Constraints:
    * stdlib only (plus the app's own app.db / app.models imports).
    * No new verdict or tier values are ever introduced: counts cover only values
      already present in the rows. The taxonomy constants below are QUOTED from
      the existing repo taxonomy (PRODUCT_SPEC §2 + the existing risk-tier
      leaderboard) and are used ONLY to report slices that came out empty --
      never to relabel a row.
    * No writes, no network, no protected imports. Read-only.
    * Averages guard against an empty row list.

The counts are of PUBLISHED verdicts as stored on the registry rows (the trust
gate in trust_gating_override.py is applied upstream when a verdict is written),
so no per-server risk is re-derived here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api/registry", tags=["registry-report"])

# Quoted, not invented: the verdict taxonomy from PRODUCT_SPEC §2 and the risk
# tiers already used by the registry.
VERDICT_TAXONOMY = (
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
)
RISK_TIER_TAXONOMY = (
    "KNOWN_THREAT",
    "HIGH_RISK_ISOLATED",
    "CAUTION_LIMITED",
    "AMBER_UNVERIFIED",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
)

ROW_COLUMNS = (
    McpServerRegistry.server_id,
    McpServerRegistry.verdict,
    McpServerRegistry.risk_tier,
    McpServerRegistry.registry_source,
    McpServerRegistry.last_assessed,
    McpServerRegistry.confidence,
)


def _rows_from_db(db: Session) -> List[Dict[str, Any]]:
    """Read the registry columns this report needs (portable SQLAlchemy)."""
    result = db.execute(select(*ROW_COLUMNS))
    return [dict(row._mapping) for row in result.all()]


def _count(rows: List[dict], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        val = (r.get(key) or "").strip() if isinstance(r.get(key), str) else r.get(key)
        if val is None or val == "":
            continue
        out[str(val)] = out.get(str(val), 0) + 1
    return out


def _confidence_stats(rows: List[dict]) -> Dict[str, Any]:
    vals: List[float] = []
    for r in rows:
        c = r.get("confidence")
        if c is None:
            continue
        try:
            vals.append(float(c))
        except (TypeError, ValueError):
            continue
    if not vals:
        return {"min": None, "max": None, "avg": 0.0, "n": 0}
    return {"min": min(vals), "max": max(vals),
            "avg": sum(vals) / len(vals), "n": len(vals)}


def _empty_slices(by_verdict: Dict[str, int], by_risk_tier: Dict[str, int]) -> List[dict]:
    """Taxonomy slices with a zero count -- never a new verdict/tier value."""
    out: List[dict] = []
    for v in VERDICT_TAXONOMY:
        if by_verdict.get(v, 0) == 0:
            out.append({"kind": "verdict", "value": v, "count": 0})
    for t in RISK_TIER_TAXONOMY:
        if by_risk_tier.get(t, 0) == 0:
            out.append({"kind": "risk_tier", "value": t, "count": 0})
    return out


def build_report(rows: Optional[List[dict]] = None, db: Optional[Session] = None) -> Dict[str, Any]:
    """Verdict / risk-tier mix of the MCP registry.

    rows: pre-fetched registry rows (see module docstring). If None and no db
    session is given, the rows are read from mcp_server_registry via app.db.
    """
    if rows is None:
        if db is not None:
            rows = _rows_from_db(db)
        else:
            gen = get_session()
            session = next(gen)
            try:
                rows = _rows_from_db(session)
            finally:
                gen.close()
    rows = list(rows or [])

    by_verdict = _count(rows, "verdict")
    by_risk_tier = _count(rows, "risk_tier")
    by_source = _count(rows, "registry_source")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(rows),
        "by_verdict": by_verdict,
        "by_risk_tier": by_risk_tier,
        "by_source": by_source,
        "confidence": _confidence_stats(rows),
        "empty_slices": _empty_slices(by_verdict, by_risk_tier),
    }


@router.get("/verdict-mix")
def verdict_mix(db: Session = Depends(get_session)) -> dict:
    """Dashboard-facing verdict / risk-tier distribution for the whole registry."""
    return build_report(db=db)


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # --- acceptance: 4 synthetic rows, 2 verdicts, 2 risk tiers, 1 source ---
    synthetic = [
        {"server_id": "s1", "verdict": "TRUSTED_GENERAL", "risk_tier": "LOW",
         "registry_source": "official", "last_assessed": None, "confidence": 0.9},
        {"server_id": "s2", "verdict": "TRUSTED_GENERAL", "risk_tier": "LOW",
         "registry_source": "official", "last_assessed": None, "confidence": 0.7},
        {"server_id": "s3", "verdict": "CAUTION_LIMITED", "risk_tier": "HIGH",
         "registry_source": "official", "last_assessed": None, "confidence": 0.5},
        {"server_id": "s4", "verdict": "CAUTION_LIMITED", "risk_tier": "HIGH",
         "registry_source": "official", "last_assessed": None, "confidence": 0.3},
    ]
    rep = build_report(synthetic)
    assert rep["total"] == 4, rep
    assert sum(rep["by_verdict"].values()) == 4, rep
    assert rep["by_verdict"]["TRUSTED_GENERAL"] == 2, rep
    assert rep["by_verdict"]["CAUTION_LIMITED"] == 2, rep
    assert len(rep["by_risk_tier"]) == 2, rep
    assert len(rep["by_source"]) == 1, rep
    expected_avg = (0.9 + 0.7 + 0.5 + 0.3) / 4
    assert abs(rep["confidence"]["avg"] - expected_avg) < 1e-9, rep
    assert rep["confidence"]["n"] == 4, rep
    assert rep["confidence"]["min"] == 0.3 and rep["confidence"]["max"] == 0.9, rep
    assert isinstance(rep["empty_slices"], list), rep

    # edge case: empty input must not blow up
    empty = build_report([])
    assert empty["total"] == 0, empty
    assert empty["by_verdict"] == {} and empty["by_source"] == {}, empty
    assert empty["confidence"]["avg"] == 0.0 and empty["confidence"]["n"] == 0, empty

    # --- DB path: real app.db session, overridden with in-memory SQLite ---
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                             url="https://github.com/stripe/agent-toolkit",
                             registry_source="official", verdict="TRUSTED_GENERAL",
                             risk_tier="LOW", confidence=0.92))
    s.add(McpServerRegistry(server_id="srv2", name="Unknown MCP",
                             url="https://example.com/mcp",
                             registry_source="community", verdict="CAUTION_LIMITED",
                             risk_tier="HIGH", confidence=0.44))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    r = c.get("/api/registry/verdict-mix"); assert r.status_code == 200, r.text
    j = r.json()
    assert j["total"] == 2, j
    assert j["by_verdict"] == {"TRUSTED_GENERAL": 1, "CAUTION_LIMITED": 1}, j
    assert j["by_source"] == {"official": 1, "community": 1}, j
    assert abs(j["confidence"]["avg"] - (0.92 + 0.44) / 2) < 1e-9, j
    print(json.dumps(j, indent=2))
    print("PASS")
