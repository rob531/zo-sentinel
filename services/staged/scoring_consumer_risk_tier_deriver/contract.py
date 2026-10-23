"""
services/staged/scoring_consumer_risk_tier_deriver/contract.py

FastAPI contract for deriving a server's risk tier from LLM axis scores and
persisting it via the write‑service HTTP endpoint.
"""

from __future__ import annotations

import json
from typing import List

import httpx
from fastapi import Depends, FastAPI, HTTPException, Path
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# Real application data layer – must be used in production.
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------
app = FastAPI(title="scoring_consumer_risk_tier_deriver")


# ----------------------------------------------------------------------
# Helper: compute a risk tier from a collection of axis scores.
# ----------------------------------------------------------------------
def _compute_risk_tier(scores: List[McpLlmAxisScore]) -> str:
    """
    Very simple tier derivation:
      - if any score has p_critical > 0.5 → "critical"
      - elif any score has p_danger   > 0.5 → "danger"
      - else                                      → "low"
    """
    for s in scores:
        if getattr(s, "p_critical", 0) > 0.5:
            return "critical"
    for s in scores:
        if getattr(s, "p_danger", 0) > 0.5:
            return "danger"
    return "low"


# ----------------------------------------------------------------------
# Helper: persist the derived tier via the write‑service.
# ----------------------------------------------------------------------
def _write_risk_tier(server_id: str, tier: str) -> None:
    """
    POST the new tier to the write‑service.  The write‑service expects a JSON
    payload with ``table`` and ``values`` keys.
    """
    payload = {
        "table": "mcp_server_registry",
        "values": {"server_id": server_id, "risk_tier": tier},
    }
    resp = httpx.post("http://127.0.0.1:8772/write", json=payload, timeout=5.0)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"write‑service failed with status {resp.status_code}",
        )


# ----------------------------------------------------------------------
# Endpoint: derive and write risk tier for a given server.
# ----------------------------------------------------------------------
@app.post("/derive/{server_id}")
def derive_and_write_risk_tier(
    server_id: str = Path(..., description="The server identifier"),
    session: Session = Depends(get_session),
) -> dict:
    """
    Read all ``McpLlmAxisScore`` rows for *server_id*, compute a risk tier,
    and persist it via the write‑service.
    """
    scores = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )
    if not scores:
        raise HTTPException(status_code=404, detail="No axis scores for server")

    tier = _compute_risk_tier(scores)
    _write_risk_tier(server_id, tier)

    return {"server_id": server_id, "risk_tier": tier}


# ----------------------------------------------------------------------
# Self‑test -------------------------------------------------------------
# ----------------------------------------------------------------------
if __name__ == "__main__":
    """
    Self‑test runnable with:
        python -m services.staged.scoring_consumer_risk_tier_deriver.contract

    The test creates an in‑memory SQLite DB, injects a few rows, monkey‑patches
    the HTTP call to the write‑service, invokes the endpoint via TestClient,
    and verifies that the derived tier matches expectations.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------
    # Build an in‑memory SQLite engine that mimics the real DB schema.
    # ------------------------------------------------------------------
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Import the metadata from the real models and create tables.
    from app.models import Base  # type: ignore

    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    # ------------------------------------------------------------------
    # Monkey‑patch httpx.post so no external service is called.
    # ------------------------------------------------------------------
    def _dummy_post(url: str, json: dict, timeout: float = 5.0):
        # Record the payload for later inspection.
        _dummy_post.last_payload = json
        return type("Resp", (), {"status_code": 200})()

    _dummy_post.last_payload = None  # type: ignore
    httpx.post = _dummy_post  # type: ignore

    # ------------------------------------------------------------------
    # Dependency override for the FastAPI app.
    # ------------------------------------------------------------------
    def _override_get_session() -> Session:
        return TestSession()

    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # ------------------------------------------------------------------
    # Seed test data.
    # ------------------------------------------------------------------
    with TestSession() as sess:
        # Server registry entry (must exist for the write‑service payload to be
        # realistic, though the endpoint does not read it directly).
        sess.add(
            McpServerRegistry(
                server_id="srv-123",
                name="test-server",
                risk_tier=None,
                confidence=None,
                description=None,
                first_seen=None,
                last_assessed=None,
                last_scanned=None,
                last_seen=None,
                meta=None,
                registry_source=None,
                scan_count=None,
                trust_score=None,
                url=None,
                verdict=None,
                verdict_reasoning=None,
            )
        )
        # Axis scores that will trigger the "critical" tier.
        sess.add_all(
            [
                McpLlmAxisScore(
                    server_id="srv-123",
                    axis_name="example",
                    adapter_sha256="a" * 64,
                    decision_rule_version="v1",
                    escalated=False,
                    escalated_to=None,
                    id=1,
                    label="label",
                    label_index=0,
                    model_version="m1",
                    p_critical=0.7,
                    p_danger=0.1,
                    p_top=0.2,
                    probs=json.dumps({}),
                    scored_at=None,
                ),
                McpLlmAxisScore(
                    server_id="srv-123",
                    axis_name="example2",
                    adapter_sha256="b" * 64,
                    decision_rule_version="v1",
                    escalated=False,
                    escalated_to=None,
                    id=2,
                    label="label2",
                    label_index=1,
                    model_version="m1",
                    p_critical=0.2,
                    p_danger=0.6,
                    p_top=0.2,
                    probs=json.dumps({}),
                    scored_at=None,
                ),
            ]
        )
        sess.commit()

    # ------------------------------------------------------------------
    # Invoke the endpoint.
    # ------------------------------------------------------------------
    response = client.post("/derive/srv-123")
    if response.status_code != 200:
        raise SystemExit(f"FAIL – endpoint returned {response.status_code}")

    payload = _dummy_post.last_payload  # type: ignore
    expected = {"table": "mcp_server_registry", "values": {"server_id": "srv-123", "risk_tier": "critical"}}
    if payload != expected:
        raise SystemExit(f"FAIL – write payload mismatch: {payload!r}")

    print("PASS")
    raise SystemExit(0)