"""axis_escalation_report.py -- fleet-wide ESCALATION report over the SFT axis scores.

Answers "which axes are tripping escalation, to what, and for which servers" by
reading the real app data layer: McpLlmAxisScore (mcp_llm_axis_scores) plus
McpServerRegistry (mcp_server_registry) for display names.

Read-only: no writes, no network, no import-time I/O. Importable as
`axis_escalation_report`.
"""
# deps: fastapi, sqlalchemy
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

router = APIRouter(prefix="/api/reports", tags=["escalation"])


@contextmanager
def _session_scope(db: Optional[Session]) -> Iterator[Session]:
    """Use the caller's session when given, else open one via the app dependency."""
    if db is not None:
        yield db
        return
    gen = get_session()
    s: Session = next(gen)
    try:
        yield s
    finally:
        s.close()
        try:
            next(gen)
        except StopIteration:
            pass


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _clamp(value: int, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = lo
    return max(lo, min(v, hi))


def generate_escalation_report(days: int = 30, top_n: int = 25,
                               db: Optional[Session] = None) -> dict:
    """Summarise axis escalations across the fleet.

    Returns:
        {"generated_at": iso8601, "days": int, "total_escalations": int,
         "by_axis": [{"axis_name", "escalated_to", "count", "mean_p_top",
                      "servers": [{"server_id", "name", "risk_tier", "p_top",
                                   "p_critical", "escalated_to", "scored_at"}]}],
         "decision_rule_versions": {version: count}}
    """
    days = _clamp(days, 1, 365)
    top_n = _clamp(top_n, 1, 200)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    with _session_scope(db) as session:
        rows = session.execute(
            select(McpLlmAxisScore,
                   McpServerRegistry.name,
                   McpServerRegistry.risk_tier)
            .outerjoin(McpServerRegistry,
                       McpServerRegistry.server_id == McpLlmAxisScore.server_id)
            .where(
                McpLlmAxisScore.escalated.is_(True),
                McpLlmAxisScore.axis_name.in_(AXES),
                McpLlmAxisScore.scored_at >= cutoff,
            )
        ).all()

    groups: dict = defaultdict(list)
    rule_versions: dict = defaultdict(int)
    for score, name, risk_tier in rows:
        groups[(score.axis_name, score.escalated_to)].append({
            "server_id": score.server_id,
            "name": name,
            "risk_tier": risk_tier,
            "p_top": float(score.p_top) if score.p_top is not None else 0.0,
            "p_critical": (float(score.p_critical) if score.p_critical is not None
                           else None),
            "escalated_to": score.escalated_to,
            "scored_at": _iso(score.scored_at),
        })
        rule_versions[score.decision_rule_version or "unknown"] += 1

    by_axis: List[dict] = []
    for (axis_name, escalated_to), servers in groups.items():
        servers.sort(key=lambda s: (-s["p_top"], s["server_id"]))
        p_tops = [s["p_top"] for s in servers]
        by_axis.append({
            "axis_name": axis_name,
            "escalated_to": escalated_to,
            "count": len(servers),
            "mean_p_top": (sum(p_tops) / len(p_tops)) if p_tops else 0.0,
            "servers": servers[:top_n],
        })
    by_axis.sort(key=lambda a: (-a["count"], a["axis_name"], a["escalated_to"] or ""))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "total_escalations": len(rows),
        "by_axis": by_axis,
        "decision_rule_versions": dict(rule_versions),
    }


@router.get("/axis-escalation")
def axis_escalation_report(days: int = 30, top_n: int = 25,
                            db: Session = Depends(get_session)) -> dict:
    """Read-only HTTP view of the escalation report."""
    return generate_escalation_report(days=days, top_n=top_n, db=db)


def _self_test() -> int:
    from fastapi import FastAPI
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
    s.add(McpServerRegistry(server_id="srv1", name="Test MCP",
                            url="https://example.com/mcp", risk_tier="HIGH"))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                          label="HIGH", model_version="v3.0_40974559",
                          escalated=True, escalated_to="HIGH", p_top=0.91,
                          p_critical=0.80, p_danger=0.70,
                          decision_rule_version="v1", scored_at=now))
    s.add(McpLlmAxisScore(id=2, server_id="srv2", axis_name="data_sensitivity",
                          label="CRITICAL", model_version="v3.0_40974559",
                          escalated=True, escalated_to=None, p_top=0.72,
                          p_critical=0.66, p_danger=0.55,
                          decision_rule_version="v1", scored_at=now))
    s.add(McpLlmAxisScore(id=3, server_id="srv1", axis_name="auth_strength",
                          label="WEAK", model_version="v3.0_40974559",
                          escalated=False, p_top=0.44, scored_at=now))
    s.add(McpLlmAxisScore(id=4, server_id="srv2", axis_name="exploit_surface",
                          label="MODERATE", model_version="v3.0_40974559",
                          escalated=False, p_top=0.21, scored_at=now))
    s.commit()

    rep = generate_escalation_report(days=30, top_n=25, db=s)
    assert rep["total_escalations"] == 2, rep
    assert rep["days"] == 30, rep
    axes = {a["axis_name"]: a for a in rep["by_axis"]}
    assert set(axes) == {"overall_risk", "data_sensitivity"}, rep
    assert axes["overall_risk"]["count"] == 1, rep
    assert axes["data_sensitivity"]["count"] == 1, rep
    assert axes["overall_risk"]["escalated_to"] == "HIGH", rep
    assert axes["data_sensitivity"]["escalated_to"] is None, rep
    assert "auth_strength" not in axes and "exploit_surface" not in axes, rep
    srv = axes["overall_risk"]["servers"][0]
    assert srv["server_id"] == "srv1" and srv["name"] == "Test MCP", srv
    assert srv["risk_tier"] == "HIGH", srv
    assert abs(srv["p_top"] - 0.91) < 1e-9, srv
    missing = axes["data_sensitivity"]["servers"][0]
    assert missing["name"] is None and missing["risk_tier"] is None, missing
    assert rep["decision_rule_versions"] == {"v1": 2}, rep
    datetime.fromisoformat(rep["generated_at"])  # ISO 8601
    assert axes["overall_risk"]["mean_p_top"] > 0.9, rep

    # clamping + edge cases
    assert generate_escalation_report(days=0, top_n=0, db=s)["days"] == 1
    assert generate_escalation_report(days=9999, top_n=9999, db=s)["days"] == 365
    empty = generate_escalation_report(days=1, top_n=1, db=s)
    assert empty["total_escalations"] == 2, empty
    capped = generate_escalation_report(days=30, top_n=1, db=s)
    assert all(len(a["servers"]) <= 1 for a in capped["by_axis"]), capped
    s.close()

    # HTTP surface over the same in-memory store (real dependency override)
    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    r = c.get("/api/reports/axis-escalation?days=30&top_n=25")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["total_escalations"] == 2, j
    assert len(j["by_axis"]) == 2, j
    print("PASS")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Fleet axis-escalation report")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--top-n", type=int, default=None)
    args = p.parse_args()
    rc = _self_test()
    if args.days is not None or args.top_n is not None:
        rep = generate_escalation_report(days=args.days or 30,
                                         top_n=args.top_n or 25)
        print(json.dumps(rep, indent=2, default=str))
    raise SystemExit(rc)
