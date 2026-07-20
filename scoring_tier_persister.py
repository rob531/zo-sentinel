# deps: requests
"""FastAPI scoring consumer daemon: reads mcp_llm_axis_scores, computes risk tiers,
and upserts risk_tier back into mcp_server_registry via write_service.

run() entry point; heartbeat to service_health every 60s.
"""
from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

WRITE_SERVICE = "http://127.0.0.1:8772"
HEARTBEAT_INTERVAL = 60
SHUTDOWN = threading.Event()

AXES = (
    "overall_risk", "auth_strength", "capability_breadth",
    "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _compute_tier(server_id: str, session: Session) -> Optional[str]:
    """Read all 7 axes for server_id and return one of 6 tier strings or None.

    Tier rules (PRODUCT_SPEC §2 + §3):
      1. CRITICAL override: any axis label==CRITICAL and p_critical>0.7 -> HIGH_RISK_ISOLATED
      2. All 7 axes present AND p_top>0.5 on majority -> TRUSTED_GENERAL
      3. overall_risk p_top>0.5 -> threshold-based tier
      4. Default -> ENTERPRISE_CONTROLLED
    """
    mv = _latest_model_version(session, server_id)
    if mv is None:
        return None

    rows = session.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    if not rows:
        return None

    axes: Dict[str, dict] = {r.axis_name: r for r in rows}

    # Rule 1: CRITICAL override
    for ax_name, row in axes.items():
        lbl = (row.label or "").upper()
        pc = row.p_critical or 0.0
        if lbl == "CRITICAL" and pc > 0.7:
            return "HIGH_RISK_ISOLATED"

    # Build ordered list
    ordered = [axes.get(ax) for ax in AXES]
    if all(o is not None for o in ordered):
        # Rule 2: all 7 axes present and majority p_top > 0.5
        p_top_vals = [o.p_top or 0.0 for o in ordered]
        majority = sum(1 for p in p_top_vals if p > 0.5) >= 4
        if majority:
            return "TRUSTED_GENERAL"

    # Rule 3: overall_risk p_top > 0.5 -> threshold-based
    ov = axes.get("overall_risk")
    if ov and (ov.p_top or 0.0) > 0.5:
        idx = ov.label_index or 0
        score = (idx / 3.0) * 100.0
        if score > 60:
            return "TRUSTED_RESEARCH"
        elif score > 45:
            return "ENTERPRISE_CONTROLLED"
        elif score > 30:
            return "CAUTION_LIMITED"
        elif score > 15:
            return "HIGH_RISK_ISOLATED"
        else:
            return "HIGH_RISK"

    # Rule 4: default safe
    return "ENTERPRISE_CONTROLLED"


def _unscored_server_ids(session: Session) -> List[str]:
    sub = select(McpServerRegistry.server_id).where(
        McpServerRegistry.risk_tier.isnot(None),
        McpServerRegistry.risk_tier != ""
    )
    rows = session.execute(
        select(McpLlmAxisScore.server_id)
        .where(McpLlmAxisScore.server_id.not_in(sub))
        .distinct()
    ).all()
    return [r[0] for r in rows]


def _tier_reasoning(tier: str) -> str:
    ts = datetime.now(timezone.utc).isoformat()
    return f"Scoring tier auto-assigned {ts}: {tier}"


def _send_heartbeat(status: str = "ok", detail: str = "") -> None:
    try:
        requests.post(
            f"{WRITE_SERVICE}/execute",
            json={
                "sql": (
                    "INSERT INTO service_health(service_name, status, meta, updated_at) "
                    "VALUES (:name, :status, :meta, now()) "
                    "ON CONFLICT (service_name) DO UPDATE SET "
                    "status = EXCLUDED.status, meta = EXCLUDED.meta, updated_at = now()"
                ),
                "params": {
                    "name": "scoring_tier_persister",
                    "status": status,
                    "meta": detail,
                },
                "wait": False,
            },
            timeout=5,
        )
    except Exception as exc:
        logger.warning("Heartbeat failed: %s", exc)


def _upsert_tier(server_id: str, tier: str, reasoning: str) -> bool:
    ts = datetime.now(timezone.utc).isoformat()
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{WRITE_SERVICE}/execute",
                json={
                    "sql": (
                        "UPDATE mcp_server_registry "
                        "SET risk_tier = :tier, verdict_reasoning = :reason, "
                        "last_assessed = :ts "
                        "WHERE server_id = :sid"
                    ),
                    "params": {
                        "tier": tier,
                        "reason": reasoning,
                        "ts": ts,
                        "sid": server_id,
                    },
                    "wait": True,
                },
                timeout=10,
            )
            if resp.status_code < 500:
                return True
        except requests.RequestException as exc:
            logger.warning("Upsert attempt %d failed for %s: %s", attempt + 1, server_id, exc)
        time.sleep(0.5 * (2 ** attempt))
    return False


def _cycle(session: Session) -> int:
    ids = _unscored_server_ids(session)
    if not ids:
        return 0
    processed = 0
    for sid in ids:
        if SHUTDOWN.is_set():
            break
        tier = _compute_tier(sid, session)
        if tier:
            reasoning = _tier_reasoning(tier)
            if _upsert_tier(sid, tier, reasoning):
                logger.info("server_id=%s tier=%s", sid, tier)
                processed += 1
    return processed


def run() -> None:
    def _sigterm(signum, _frame):
        logger.info("SIGTERM received, shutting down")
        SHUTDOWN.set()
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    logger.info("scoring_tier_persister starting")
    from app.db import SessionLocal
    session: Session = SessionLocal()
    try:
        while not SHUTDOWN.is_set():
            heartbeat_at = time.time()
            try:
                processed = _cycle(session)
                _send_heartbeat("ok", f"cycle_ok servers={processed}")
            except Exception as exc:
                logger.exception("Cycle failed: %s", exc)
                _send_heartbeat("error", str(exc)[:200])
            elapsed = time.time() - heartbeat_at
            sleep_for = max(0, HEARTBEAT_INTERVAL - elapsed)
            SHUTDOWN.wait(timeout=sleep_for)
    finally:
        session.close()
        logger.info("scoring_tier_persister stopped")


# ---------------------------------------------------------------------------
# Self-test: SQLite via dependency override, 3 known servers
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    _captured: Dict[str, list] = {}

    class _EmptyResponse:
        status_code: int
        def __init__(self, code): self.status_code = code
        def json(self): return {}
        def raise_for_status(self): pass

    _orig_post = requests.post

    def _mock_post(url: str, **kwargs):
        body = kwargs.get("json", {})
        sql = body.get("sql", "")
        params = body.get("params", {})
        if "service_health" in sql:
            return _EmptyResponse(200)
        if "UPDATE mcp_server_registry" in sql:
            _captured.setdefault("updates", []).append(params)
            return _EmptyResponse(200)
        return _EmptyResponse(500)

    requests.post = _mock_post

    try:
        eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
        Base.metadata.create_all(eng)
        TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
        s = TS()

        # Server 1: TRUSTED_GENERAL -- all 7 axes, p_top=0.6 on all (majority >0.5)
        srv_trusted = "srv_trusted"
        s.add(McpServerRegistry(server_id=srv_trusted, name="Trusted MCP",
                                url="https://github.com/stripe/agent-toolkit"))
        for i, ax in enumerate(AXES, start=1):
            s.add(McpLlmAxisScore(id=i, server_id=srv_trusted, axis_name=ax,
                                  label="LOW", p_top=0.6,
                                  model_version="v3.0_40974559"))

        # Server 2: CAUTION_LIMITED -- overall_risk p_top=0.55 (Rule 3 fires),
        # but most axes p_top<0.5 so Rule 2 (majority p_top>0.5) does NOT fire.
        # label_index=1 -> score=(1/3)*100=33.3 -> 30<33.3<=45 -> CAUTION_LIMITED
        srv_caution = "srv_caution"
        s.add(McpServerRegistry(server_id=srv_caution, name="Caution MCP",
                                url="https://example.com/mcp"))
        base = len(AXES) + 1
        for i, ax in enumerate(AXES, start=base):
            if ax == "overall_risk":
                s.add(McpLlmAxisScore(id=i, server_id=srv_caution, axis_name=ax,
                                      label="HIGH", label_index=1, p_top=0.55,
                                      model_version="v3.0_40974559"))
            else:
                # p_top<=0.5 so Rule 2 (majority) does not fire; still counts as "present"
                s.add(McpLlmAxisScore(id=i, server_id=srv_caution, axis_name=ax,
                                      label="MEDIUM", label_index=1, p_top=0.4,
                                      model_version="v3.0_40974559"))

        # Server 3: INSUFFICIENT -- no axis scores
        srv_insuff = "srv_insuff"
        s.add(McpServerRegistry(server_id=srv_insuff, name="No Scores MCP",
                                url="https://example.com/noscores"))

        s.commit()
        s.close()

        # Exercise _compute_tier directly
        s = TS()
        tier_trusted = _compute_tier(srv_trusted, s)
        assert tier_trusted == "TRUSTED_GENERAL", f"trusted: {tier_trusted}"

        tier_caution = _compute_tier(srv_caution, s)
        assert tier_caution == "CAUTION_LIMITED", f"caution: {tier_caution}"

        tier_insuff = _compute_tier(srv_insuff, s)
        assert tier_insuff is None, f"insufficient: {tier_insuff}"
        s.close()

        # Verify cycle path with upsert capture
        s = TS()
        _captured.clear()
        import scoring_tier_persister as _mod
        _orig_upsert = _mod._upsert_tier

        def _mock_upsert(sid, tier, reason):
            _captured.setdefault("updates", []).append(
                {"tier": tier, "reason": reason, "sid": sid})
            return True
        _mod._upsert_tier = _mock_upsert

        try:
            processed = _cycle(s)
            # srv_trusted and srv_caution have scores; srv_insuff does not
            assert processed == 2, f"expected 2 processed, got {processed}"
            updates = _captured.get("updates", [])
            assert len(updates) == 2, f"expected 2 upserts, got {updates}"
            tids = {u["tier"] for u in updates}
            assert "TRUSTED_GENERAL" in tids, tids
            assert "CAUTION_LIMITED" in tids, tids
        finally:
            _mod._upsert_tier = _orig_upsert
            s.close()

        print("PASS")
    finally:
        requests.post = _orig_post
