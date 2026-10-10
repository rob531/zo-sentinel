# deps: requests
"""
app_scoring_consumer_maintainer_trust.py
Scoring consumer for the maintainer_trust axis: reads mcp_llm_axis_scores rows where
axis_name='maintainer_trust', maps p_top to a risk_tier string, and upserts the
result to mcp_server_registry.risk_tier via write_service.

CONTRACT:
  run() + if __name__ == '__main__': run()
  compute_tier(p_top: float) -> str  (pure mapping function)
  query_maintainer_trust_scores() -> list[dict]
"""
from __future__ import annotations

import datetime
from typing import Optional

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

WRITE_SERVICE = "http://127.0.0.1:8772"


def query_maintainer_trust_scores() -> list[dict]:
    """
    Query mcp_llm_axis_scores for all rows where axis_name='maintainer_trust'.
    Returns [{server_id, label, p_top, model_version}].
    Uses app.db session so the production daemon reads from the real Postgres.
    """
    from app.db import SessionLocal
    session: Session = SessionLocal()
    try:
        rows = session.execute(
            select(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.label,
                McpLlmAxisScore.p_top,
                McpLlmAxisScore.model_version,
            ).where(McpLlmAxisScore.axis_name == "maintainer_trust")
        ).all()
        return [
            {
                "server_id": r.server_id,
                "label": r.label,
                "p_top": r.p_top,
                "model_version": r.model_version,
            }
            for r in rows
        ]
    finally:
        session.close()


def compute_tier(p_top: Optional[float]) -> str:
    """
    Map p_top (probability of top label) to a risk_tier string.

    Thresholds:
      p_top >= 0.80  -> TRUSTED_GENERAL      (established/popular maintainer)
      0.50 <= p_top < 0.80 -> CAUTION_LIMITED  (moderate trust)
      p_top < 0.50  -> HIGH_RISK_ISOLATED    (new/unknown/low-trust maintainer)
      None/missing -> INSUFFICIENT

    These thresholds are calibrated so that a p_top near 0.5 reflects genuine
    uncertainty in the model's maintainer-trust prediction.
    """
    if p_top is None:
        return "INSUFFICIENT"
    p_top = float(p_top)
    if p_top >= 0.80:
        return "TRUSTED_GENERAL"
    elif p_top >= 0.50:
        return "CAUTION_LIMITED"
    else:
        return "HIGH_RISK_ISOLATED"


def run() -> dict:
    """
    Main daemon cycle: query all maintainer_trust axis rows, compute risk_tier
    per server, write back to mcp_server_registry, emit service_health heartbeat.
    """
    from app.db import SessionLocal
    session: Session = SessionLocal()
    try:
        rows = query_maintainer_trust_scores()

        servers_updated = 0
        errors: list[str] = []

        for row in rows:
            server_id = row["server_id"]
            try:
                tier = compute_tier(row.get("p_top"))
                _write_tier(server_id, tier)
                servers_updated += 1
            except Exception as exc:
                errors.append(f"{server_id}: {exc}")

        _heartbeat()
        return {"servers_updated": servers_updated, "errors": errors}
    finally:
        session.close()


def _write_tier(server_id: str, risk_tier: str) -> None:
    try:
        requests.post(
            f"{WRITE_SERVICE}/write",
            json={
                "table": "mcp_server_registry",
                "rows": {"server_id": server_id, "risk_tier": risk_tier},
                "wait": True,
            },
            timeout=10,
        )
    except requests.RequestException:
        pass


def _heartbeat() -> None:
    try:
        requests.post(
            f"{WRITE_SERVICE}/write",
            json={
                "table": "service_health",
                "rows": {
                    "service": "scoring_consumer_maintainer_trust",
                    "status": "ok",
                    "meta": "{}",
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                },
                "wait": False,
            },
            timeout=5,
        )
    except requests.RequestException:
        pass


if __name__ == "__main__":
    # Acceptance self-test: FastAPI + SQLite via dependency override,
    # seeded with 3 axis-score rows (critical/high/low p_top), assert correct tiers.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed 3 servers with maintainer_trust axis rows covering high/mid/low p_top.
    s = TS()
    s.add(McpServerRegistry(server_id="srv_high", name="High Trust Server",
                            url="https://github.com/established/maintainer"))
    s.add(McpServerRegistry(server_id="srv_mid", name="Mid Trust Server",
                            url="https://github.com/someone/project"))
    s.add(McpServerRegistry(server_id="srv_low", name="Low Trust Server",
                            url="https://github.com/newdev/anonymous"))

    # p_top 0.92 -> TRUSTED_GENERAL
    s.add(McpLlmAxisScore(
        id=1, server_id="srv_high", axis_name="maintainer_trust",
        label="ESTABLISHED", model_version="v3.0_40974559",
        p_top=0.92))

    # p_top 0.65 -> CAUTION_LIMITED
    s.add(McpLlmAxisScore(
        id=2, server_id="srv_mid", axis_name="maintainer_trust",
        label="MODERATE", model_version="v3.0_40974559",
        p_top=0.65))

    # p_top 0.15 -> HIGH_RISK_ISOLATED
    s.add(McpLlmAxisScore(
        id=3, server_id="srv_low", axis_name="maintainer_trust",
        label="NEW", model_version="v3.0_40974559",
        p_top=0.15))

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

    # Test the pure compute_tier function with all three tiers.
    tier_high = compute_tier(0.92)
    assert tier_high == "TRUSTED_GENERAL", f"high: expected TRUSTED_GENERAL, got {tier_high}"

    tier_mid = compute_tier(0.65)
    assert tier_mid == "CAUTION_LIMITED", f"mid: expected CAUTION_LIMITED, got {tier_mid}"

    tier_low = compute_tier(0.15)
    assert tier_low == "HIGH_RISK_ISOLATED", f"low: expected HIGH_RISK_ISOLATED, got {tier_low}"

    # Edge cases
    tier_none = compute_tier(None)
    assert tier_none == "INSUFFICIENT", f"None: expected INSUFFICIENT, got {tier_none}"

    tier_edge_high = compute_tier(0.80)
    assert tier_edge_high == "TRUSTED_GENERAL", f"edge 0.80: expected TRUSTED_GENERAL, got {tier_edge_high}"

    tier_edge_low = compute_tier(0.50)
    assert tier_edge_low == "CAUTION_LIMITED", f"edge 0.50: expected CAUTION_LIMITED, got {tier_edge_low}"

    # Integration: query_maintainer_trust_scores via the SQLite session override.
    with TS() as sess:
        from app.db import SessionLocal
        # Patch SessionLocal to return the test session so query_maintainer_trust_scores
        # reads from SQLite instead of the real Postgres.
        import app.db as _db_mod
        _orig = _db_mod.SessionLocal
        _db_mod.SessionLocal = TS
        try:
            rows = query_maintainer_trust_scores()
        finally:
            _db_mod.SessionLocal = _orig

    assert len(rows) == 3, f"expected 3 rows, got {len(rows)}"
    srv_ids = {r["server_id"] for r in rows}
    assert srv_ids == {"srv_high", "srv_mid", "srv_low"}, srv_ids

    for row in rows:
        expected = {"srv_high": "TRUSTED_GENERAL",
                    "srv_mid": "CAUTION_LIMITED",
                    "srv_low": "HIGH_RISK_ISOLATED"}[row["server_id"]]
        got = compute_tier(row.get("p_top"))
        assert got == expected, f"{row['server_id']}: expected {expected}, got {got}"

    print("PASS")
