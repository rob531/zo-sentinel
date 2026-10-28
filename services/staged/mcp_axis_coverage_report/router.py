"""mcp_axis_coverage_report.py -- READ-ONLY coverage report over the app Postgres.

Finds MCP servers in mcp_server_registry that have NO rows in mcp_llm_axis_scores
(i.e. servers the LLM student model never scored) and reports coverage per
registry source.

Data layer: the REAL app session (app.db / app.models). No writes of any kind --
this module only SELECTs. All SQL is Postgres-portable.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["coverage"])

SAMPLE_CAP = 50


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _empty_report(error: Optional[str] = None) -> Dict[str, Any]:
    rep: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_servers": 0,
        "scored_servers": 0,
        "unscored_servers": 0,
        "coverage_pct": 0.0,
        "by_registry_source": {},
        "sample_unscored": [],
    }
    if error:
        rep["error"] = error
    return rep


def build_report(session: Optional[Session] = None) -> Dict[str, Any]:
    """Coverage report over the registry. Read-only.

    session=None -> a session from the real app.db engine.
    Missing table / DB error -> dict with an 'error' key and empty aggregates.
    """
    owns = session is None
    if owns:
        session = SessionLocal()
    try:
        # servers that carry at least one axis score (max p_top is the run's top prob)
        scored_rows = session.execute(
            select(McpLlmAxisScore.server_id,
                   func.max(McpLlmAxisScore.p_top).label("top_p"))
            .group_by(McpLlmAxisScore.server_id)
        ).all()
        scored_ids = {r[0] for r in scored_rows}

        registry_rows = session.execute(
            select(McpServerRegistry.server_id,
                   McpServerRegistry.name,
                   McpServerRegistry.registry_source,
                   McpServerRegistry.risk_tier,
                   McpServerRegistry.last_assessed)
        ).all()
    except Exception as exc:
        return _empty_report(error=f"{type(exc).__name__}: {exc}")
    finally:
        if owns:
            try:
                session.close()
            except Exception:
                pass

    total = len(registry_rows)
    scored = sum(1 for r in registry_rows if r[0] in scored_ids)
    unscored = total - scored

    by_source: Dict[str, Dict[str, int]] = {}
    unscored_rows = []
    for sid, name, source, risk_tier, last_assessed in registry_rows:
        key = source or "unknown"
        agg = by_source.setdefault(key, {"total": 0, "scored": 0, "unscored": 0})
        agg["total"] += 1
        if sid in scored_ids:
            agg["scored"] += 1
        else:
            agg["unscored"] += 1
            unscored_rows.append({
                "server_id": sid,
                "name": name,
                "risk_tier": risk_tier,
                "last_assessed": _iso(last_assessed),
            })

    unscored_rows.sort(key=lambda r: (r["server_id"] or ""))
    coverage_pct = round((scored / total) * 100.0, 2) if total else 0.0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_servers": total,
        "scored_servers": scored,
        "unscored_servers": unscored,
        "coverage_pct": coverage_pct,
        "by_registry_source": by_source,
        "sample_unscored": unscored_rows[:SAMPLE_CAP],
    }


@router.get("/mcp-axis-coverage")
def axis_coverage(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Read-only coverage endpoint: registry servers the LLM student never scored."""
    return build_report(session=db)


def main() -> Dict[str, Any]:
    report = build_report()
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


if __name__ == "__main__":  # self-test: real imports, in-memory SQLite session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng, tables=[McpServerRegistry.__table__,
                                          McpLlmAxisScore.__table__])
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()

    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            registry_source="github",
                            url="https://github.com/stripe/agent-toolkit",
                            risk_tier="MEDIUM"))
    s.add(McpServerRegistry(server_id="srv2", name="Acme MCP",
                            registry_source="github",
                            url="https://github.com/acme/mcp",
                            risk_tier="HIGH"))
    s.add(McpServerRegistry(server_id="srv3", name="Fresh MCP",
                            registry_source="smithery",
                            url="https://smithery.ai/fresh",
                            risk_tier="LOW"))
    _i = 0
    for sid in ("srv1", "srv2"):
        for ax, lbl in (("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
                        ("capability_breadth", "BROAD"), ("data_sensitivity", "CRITICAL"),
                        ("network_egress", "EXTERNAL"), ("maintainer_trust", "ESTABLISHED"),
                        ("exploit_surface", "MODERATE")):
            _i += 1
            s.add(McpLlmAxisScore(id=_i, server_id=sid, axis_name=ax, label=lbl,
                                  model_version="v3.0_40974559"))
    s.commit()

    rep = build_report(session=s)
    assert rep["total_servers"] == 3, rep
    assert rep["scored_servers"] == 2, rep
    assert rep["unscored_servers"] == 1, rep
    assert rep["coverage_pct"] == 66.67, rep
    assert len(rep["sample_unscored"]) == 1, rep
    assert rep["sample_unscored"][0]["server_id"] == "srv3", rep
    assert rep["by_registry_source"]["github"]["total"] == 2, rep
    assert rep["by_registry_source"]["github"]["scored"] == 2, rep
    assert rep["by_registry_source"]["smithery"]["unscored"] == 1, rep
    assert rep["generated_at"], rep

    # edge case: missing table -> graceful error dict, no raise
    empty_eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
    TS2 = sessionmaker(bind=empty_eng)
    s2 = TS2()
    bad = build_report(session=s2)
    assert "error" in bad, bad
    assert bad["total_servers"] == 0 and bad["sample_unscored"] == [], bad

    s.close()
    s2.close()
    print("PASS")

