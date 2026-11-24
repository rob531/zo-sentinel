"""risk_register_csv_exporter.py -- risk register CSV export (audit/compliance).

Reads the live scoring tables through the app SQLAlchemy session
(McpLlmAxisScore + McpServerRegistry) and renders the current risk register as
CSV. Pure report generator: no HTTP route, no writes, no network.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session, SessionLocal
from app.models import McpLlmAxisScore, McpServerRegistry

HEADER = ["server_id", "name", "registry_source", "risk_tier", "overall_p_top",
          "top_axis", "top_axis_p_top", "last_scored_at"]

TIER_CRITICAL, TIER_HIGH, TIER_MEDIUM, TIER_LOW = "CRITICAL", "HIGH", "MEDIUM", "LOW"

_RISK_RANK = {TIER_CRITICAL: 3, TIER_HIGH: 2, TIER_MEDIUM: 1, TIER_LOW: 0,
              "UNASSESSED": -1}


def _derive_tier(overall_p_top: Optional[float]) -> str:
    if overall_p_top is None:
        return "UNASSESSED"
    if overall_p_top >= 0.75:
        return TIER_CRITICAL
    if overall_p_top >= 0.50:
        return TIER_HIGH
    if overall_p_top >= 0.25:
        return TIER_MEDIUM
    return TIER_LOW


def _ts_key(row) -> str:
    return row.scored_at.isoformat() if getattr(row, "scored_at", None) else ""


def build_rows(session: Session) -> List[dict]:
    """Current risk register as plain dicts (one row per scored server)."""
    score_rows = session.execute(
        select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name,
               McpLlmAxisScore.label, McpLlmAxisScore.p_top,
               McpLlmAxisScore.scored_at, McpLlmAxisScore.model_version)
    ).all()
    reg_rows = session.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.name,
               McpServerRegistry.registry_source, McpServerRegistry.risk_tier)
    ).all()

    registry: Dict[str, dict] = {
        r.server_id: {"name": r.name, "registry_source": r.registry_source,
                      "risk_tier": r.risk_tier}
        for r in reg_rows
    }

    by_server: Dict[str, list] = {}
    for r in score_rows:
        by_server.setdefault(r.server_id, []).append(r)

    out: List[dict] = []
    for server_id, rows in by_server.items():
        # current model_version = the one with the most recent scored_at
        newest = max(rows, key=lambda r: (_ts_key(r), r.model_version or ""))
        mv = newest.model_version
        current = [r for r in rows if r.model_version == mv]

        overall = next((r for r in current if r.axis_name == "overall_risk"), None)
        overall_p_top = round(overall.p_top, 4) if (overall and overall.p_top is not None) else None

        others = [r for r in current
                  if r.axis_name != "overall_risk" and r.p_top is not None]
        top = max(others, key=lambda r: (r.p_top, r.axis_name)) if others else None

        reg = registry.get(server_id, {})
        tier = reg.get("risk_tier") or _derive_tier(overall_p_top)
        scored_at = max((_ts_key(r) for r in current if r.scored_at), default="")

        out.append({
            "server_id": server_id,
            "name": reg.get("name") or "",
            "registry_source": reg.get("registry_source") or "",
            "risk_tier": tier,
            "overall_p_top": overall_p_top,
            "top_axis": top.axis_name if top is not None else "",
            "top_axis_p_top": round(top.p_top, 4) if top is not None else None,
            "last_scored_at": scored_at,
        })

    out.sort(key=lambda r: (-_RISK_RANK.get(r["risk_tier"], -2), r["server_id"]))
    return out


def to_csv(rows: List[dict]) -> str:
    """Render risk-register rows as a CSV string with a fixed header order."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=HEADER, lineterminator="\n",
                            extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in HEADER})
    return buf.getvalue()


def build_rows_from_db() -> List[dict]:
    """Convenience wrapper: build the register from the live app session."""
    s = SessionLocal()
    try:
        return build_rows(s)
    finally:
        s.close()


if __name__ == "__main__":
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
    s.add(McpServerRegistry(server_id="srv1", name="Alpha MCP",
                            url="https://github.com/example/alpha",
                            registry_source="github"))
    s.add(McpServerRegistry(server_id="srv2", name="Beta MCP",
                            url="https://github.com/example/beta",
                            registry_source="smithery", risk_tier="HIGH"))
    seeds = [
        (1, "srv1", "overall_risk", "HIGH", 0.123456),
        (2, "srv1", "exploit_surface", "MODERATE", 0.4321),
        (3, "srv1", "network_egress", "EXTERNAL", 0.2),
        (4, "srv2", "overall_risk", "CRITICAL", 0.9),
        (5, "srv2", "auth_strength", "WEAK", 0.77),
    ]
    for rid, sid, axis, label, p_top in seeds:
        s.add(McpLlmAxisScore(id=rid, server_id=sid, axis_name=axis, label=label,
                              p_top=p_top, model_version="v3.0_40974559"))
    s.commit()

    rows = build_rows(s)
    assert len(rows) == 2, rows
    by_id = {r["server_id"]: r for r in rows}

    # srv1: no registry risk_tier -> derived from overall p_top (0.123456 -> LOW)
    r1 = by_id["srv1"]
    assert r1["risk_tier"] == TIER_LOW, r1
    assert r1["overall_p_top"] == 0.1235, r1
    assert r1["top_axis"] == "exploit_surface", r1
    assert r1["top_axis_p_top"] == 0.4321, r1
    assert r1["name"] == "Alpha MCP" and r1["registry_source"] == "github", r1

    # srv2: registry risk_tier wins over the derived tier
    r2 = by_id["srv2"]
    assert r2["risk_tier"] == TIER_HIGH, r2
    assert r2["overall_p_top"] == 0.9, r2
    assert r2["top_axis"] == "auth_strength", r2

    empty_csv = to_csv([])
    assert empty_csv.splitlines()[0] == ",".join(HEADER), empty_csv
    assert len(empty_csv.splitlines()) == 1, empty_csv

    csv_text = to_csv(rows)
    lines = csv_text.splitlines()
    assert lines[0] == ",".join(HEADER), lines[0]
    assert len(lines) == 3, lines
    parsed = list(csv.DictReader(io.StringIO(csv_text)))
    assert [p["server_id"] for p in parsed] == ["srv2", "srv1"], parsed
    assert parsed[0]["risk_tier"] == TIER_HIGH, parsed
    assert parsed[0]["top_axis"] == "auth_strength", parsed
    assert parsed[1]["risk_tier"] == TIER_LOW, parsed
    assert parsed[1]["overall_p_top"] == "0.1235", parsed
    s.close()

    # exercise the real session dependency seam (app.db.get_session) via a local app
    app = FastAPI()

    @app.get("/risk-register.csv")
    def _reg(db: Session = Depends(get_session)):
        return to_csv(build_rows(db))

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    r = c.get("/risk-register.csv")
    assert r.status_code == 200, r.text
    rlines = r.json().splitlines()
    assert rlines[0] == ",".join(HEADER), rlines[0]
    assert len(rlines) == 3, rlines
    print("PASS")
