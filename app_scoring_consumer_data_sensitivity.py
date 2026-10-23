# deps: requests
"""
app_scoring_consumer_data_sensitivity.py
Scoring consumer for the data_sensitivity axis: reads mcp_llm_axis_scores rows where
axis_name='data_sensitivity', maps label + p_critical to a risk_tier string, and
upserts the result to mcp_server_registry.risk_tier via write_service.

CONTRACT:
  run() + if __name__ == '__main__': run()
  compute_data_sensitivity_tier(server_id: str, label: str, p_top: float, p_critical: float) -> str
  query_data_sensitivity_scores() -> list[dict]
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


def query_data_sensitivity_scores() -> list[dict]:
    """
    Query mcp_llm_axis_scores for all rows where axis_name='data_sensitivity'.
    Returns [{server_id, label, p_top, p_critical, model_version}].
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
                McpLlmAxisScore.p_critical,
                McpLlmAxisScore.model_version,
            ).where(McpLlmAxisScore.axis_name == "data_sensitivity")
        ).all()
        return [
            {
                "server_id": r.server_id,
                "label": r.label,
                "p_top": r.p_top,
                "p_critical": r.p_critical,
                "model_version": r.model_version,
            }
            for r in rows
        ]
    finally:
        session.close()


def compute_data_sensitivity_tier(
    server_id: str, label: Optional[str], p_top: Optional[float], p_critical: Optional[float]
) -> str:
    """
    Map data_sensitivity label + p_critical to a risk_tier string.

    Label vocabulary: {none, read_only, writes_local, exfiltrates}

    LABEL -> TIER MAPPING:
      none                    -> TRUSTED_GENERAL
      read_only               -> TRUSTED_RESEARCH
      writes_local            -> ENTERPRISE_CONTROLLED
      exfiltrates             -> HIGH_RISK_ISOLATED
      exfiltrates + p_critical > 0.5 -> KNOWN_THREAT
      None/missing label       -> INSUFFICIENT
    """
    if label is None:
        return "INSUFFICIENT"
    label = str(label).strip().lower()
    p_crit = float(p_critical) if p_critical is not None else 0.0

    if label == "none":
        return "TRUSTED_GENERAL"
    elif label == "read_only":
        return "TRUSTED_RESEARCH"
    elif label == "writes_local":
        return "ENTERPRISE_CONTROLLED"
    elif label == "exfiltrates":
        if p_crit > 0.5:
            return "KNOWN_THREAT"
        return "HIGH_RISK_ISOLATED"
    else:
        return "INSUFFICIENT"


def _now_iso8601() -> str:
    return datetime.datetime.utcnow().isoformat()


def run() -> dict:
    """
    Main daemon cycle: query all data_sensitivity axis rows, compute risk_tier
    per server, write back to mcp_server_registry, emit service_health heartbeat.
    """
    from app.db import SessionLocal
    session: Session = SessionLocal()
    try:
        rows = query_data_sensitivity_scores()

        servers_updated = 0
        errors: list[str] = []
        batch: list[dict] = []

        for row in rows:
            server_id = row["server_id"]
            try:
                tier = compute_data_sensitivity_tier(
                    server_id=row["server_id"],
                    label=row.get("label"),
                    p_top=row.get("p_top"),
                    p_critical=row.get("p_critical"),
                )
                batch.append({
                    "server_id": server_id,
                    "risk_tier": tier,
                    "last_assessed": _now_iso8601(),
                })
            except Exception as exc:
                errors.append(f"{server_id}: {exc}")

        if batch:
            _write_batch(batch)

        servers_updated = len(batch)
        _heartbeat()
        return {"servers_updated": servers_updated, "errors": errors}
    finally:
        session.close()


def _write_batch(batch: list[dict]) -> None:
    """Upsert risk_tier + last_assessed for each server via write_service ON CONFLICT."""
    if not batch:
        return
    for row in batch:
        _write_tier(row["server_id"], row["risk_tier"], row["last_assessed"])


def _write_tier(server_id: str, risk_tier: str, last_assessed: str) -> None:
    try:
        requests.post(
            f"{WRITE_SERVICE}/write",
            json={
                "table": "mcp_server_registry",
                "rows": {
                    "server_id": server_id,
                    "risk_tier": risk_tier,
                    "last_assessed": last_assessed,
                },
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
                    "service": "scoring_consumer_data_sensitivity",
                    "status": "ok",
                    "meta": "{}",
                    "timestamp": _now_iso8601(),
                },
                "wait": False,
            },
            timeout=5,
        )
    except requests.RequestException:
        pass


if __name__ == "__main__":
    # Acceptance self-test: FastAPI + SQLite via dependency override,
    # seeded with 6 axis-score rows covering all 5 expected tiers.
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

    # Seed 6 servers with data_sensitivity axis rows:
    #   none x2        -> TRUSTED_GENERAL
    #   read_only x1   -> TRUSTED_RESEARCH
    #   writes_local x2 -> ENTERPRISE_CONTROLLED
    #   exfiltrates x1 (p_crit <= 0.5) -> HIGH_RISK_ISOLATED
    #   (KNOWN_THREAT is covered by compute_tier unit test below)

    s = TS()
    s.add(McpServerRegistry(server_id="srv_none_1", name="No Data Access",
                            url="https://example.com/none1"))
    s.add(McpServerRegistry(server_id="srv_none_2", name="No Data Access 2",
                            url="https://example.com/none2"))
    s.add(McpServerRegistry(server_id="srv_read_only", name="Read Only Server",
                            url="https://example.com/readonly"))
    s.add(McpServerRegistry(server_id="srv_writes_local_1", name="Writes Local 1",
                            url="https://example.com/writes1"))
    s.add(McpServerRegistry(server_id="srv_writes_local_2", name="Writes Local 2",
                            url="https://example.com/writes2"))
    s.add(McpServerRegistry(server_id="srv_exfil", name="Exfil Server",
                            url="https://example.com/exfil"))

    # id, server_id, axis_name, label, model_version (ONLY required kwargs per spec)
    s.add(McpLlmAxisScore(
        id=1, server_id="srv_none_1", axis_name="data_sensitivity",
        label="none", model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(
        id=2, server_id="srv_none_2", axis_name="data_sensitivity",
        label="none", model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(
        id=3, server_id="srv_read_only", axis_name="data_sensitivity",
        label="read_only", model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(
        id=4, server_id="srv_writes_local_1", axis_name="data_sensitivity",
        label="writes_local", model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(
        id=5, server_id="srv_writes_local_2", axis_name="data_sensitivity",
        label="writes_local", model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(
        id=6, server_id="srv_exfil", axis_name="data_sensitivity",
        label="exfiltrates", model_version="v3.0_40974559",
        p_critical=0.3))  # p_crit <= 0.5 -> HIGH_RISK_ISOLATED

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

    # Unit tests for compute_data_sensitivity_tier
    # Expected tiers: none->TRUSTED_GENERAL, read_only->TRUSTED_RESEARCH,
    #                 writes_local->ENTERPRISE_CONTROLLED,
    #                 exfiltrates->HIGH_RISK_ISOLATED,
    #                 exfiltrates+p_crit>0.5->KNOWN_THREAT

    tier_none = compute_data_sensitivity_tier("srv1", "none", 0.9, 0.0)
    assert tier_none == "TRUSTED_GENERAL", f"none: expected TRUSTED_GENERAL, got {tier_none}"

    tier_readonly = compute_data_sensitivity_tier("srv2", "read_only", 0.7, 0.1)
    assert tier_readonly == "TRUSTED_RESEARCH", f"read_only: expected TRUSTED_RESEARCH, got {tier_readonly}"

    tier_writes = compute_data_sensitivity_tier("srv3", "writes_local", 0.6, 0.2)
    assert tier_writes == "ENTERPRISE_CONTROLLED", f"writes_local: expected ENTERPRISE_CONTROLLED, got {tier_writes}"

    tier_exfil = compute_data_sensitivity_tier("srv4", "exfiltrates", 0.4, 0.3)
    assert tier_exfil == "HIGH_RISK_ISOLATED", f"exfiltrates p_crit<=0.5: expected HIGH_RISK_ISOLATED, got {tier_exfil}"

    tier_threat = compute_data_sensitivity_tier("srv5", "exfiltrates", 0.4, 0.6)
    assert tier_threat == "KNOWN_THREAT", f"exfiltrates p_crit>0.5: expected KNOWN_THREAT, got {tier_threat}"

    # Edge cases
    tier_none_pcrit = compute_data_sensitivity_tier("srv6", "none", 0.9, 0.6)
    assert tier_none_pcrit == "TRUSTED_GENERAL", f"none ignores p_crit"

    tier_missing_label = compute_data_sensitivity_tier("srv7", None, 0.9, 0.6)
    assert tier_missing_label == "INSUFFICIENT", f"None label: expected INSUFFICIENT, got {tier_missing_label}"

    tier_unknown_label = compute_data_sensitivity_tier("srv8", "unknown_label", 0.9, 0.6)
    assert tier_unknown_label == "INSUFFICIENT", f"unknown label: expected INSUFFICIENT, got {tier_unknown_label}"

    tier_missing_pcrit = compute_data_sensitivity_tier("srv9", "exfiltrates", 0.4, None)
    assert tier_missing_pcrit == "HIGH_RISK_ISOLATED", f"exfiltrates p_crit=None: expected HIGH_RISK_ISOLATED, got {tier_missing_pcrit}"

    # Integration: query_data_sensitivity_scores via the SQLite session override.
    with TS() as sess:
        from app.db import SessionLocal
        import app.db as _db_mod
        _orig = _db_mod.SessionLocal
        _db_mod.SessionLocal = TS
        try:
            rows = query_data_sensitivity_scores()
        finally:
            _db_mod.SessionLocal = _orig

    assert len(rows) == 6, f"expected 6 rows, got {len(rows)}"
    srv_ids = {r["server_id"] for r in rows}
    assert srv_ids == {"srv_none_1", "srv_none_2", "srv_read_only",
                       "srv_writes_local_1", "srv_writes_local_2", "srv_exfil"}, srv_ids

    expected_tiers = {
        "srv_none_1": "TRUSTED_GENERAL",
        "srv_none_2": "TRUSTED_GENERAL",
        "srv_read_only": "TRUSTED_RESEARCH",
        "srv_writes_local_1": "ENTERPRISE_CONTROLLED",
        "srv_writes_local_2": "ENTERPRISE_CONTROLLED",
        "srv_exfil": "HIGH_RISK_ISOLATED",
    }

    for row in rows:
        expected = expected_tiers.get(row["server_id"])
        got = compute_data_sensitivity_tier(
            server_id=row["server_id"],
            label=row.get("label"),
            p_top=row.get("p_top"),
            p_critical=row.get("p_critical"),
        )
        assert got == expected, f"{row['server_id']}: expected {expected}, got {got}"

    print("PASS")
