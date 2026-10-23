"""Two-pole test: two PUBLIC (unauthenticated) endpoints must not leak the legacy
`mcp_server_registry.verdict` column (~99.4% the string 'unknown') to anonymous
callers / crawlers. They must surface the live `risk_tier` instead -- matching the
already-shipped migration in server_scorecard_api.py / server_composite_risk_ranking_api.py,
which keep a `verdict` KEY but populate it from a risk/tier value, never the raw column.

SITES:
1. verdict_axis_detail_api.py  -- GET /verdict/{server_id}/detail  (router has NO auth dep)
   `verdict` field was `reg.verdict` (legacy column). The response already carries a
   separate computed `risk_tier` field, so the `verdict` KEY is kept but repopulated
   from the live `reg.risk_tier` column.
2. services/active/org_risk_summary/logic.py -- GET /api/org/{org_id}/risk_summary (no auth)
   `recent_verdicts[]` entries carried `verdict = getattr(srv, "verdict", None)` alongside
   an existing `risk_tier`. The `verdict` KEY is kept but repopulated from `risk_tier`.

SEED (both poles): a server with registry.verdict='unknown' but risk_tier='MEDIUM'.
  RED  (pre-fix): response exposes 'unknown'.
  GREEN (post-fix): response exposes 'MEDIUM' (risk_tier) and never the raw 'unknown'.

Note on site 2: McpServerRegistry has no org column in the current model (the service's
`_org_column()` org-resolution is a separate, pre-existing concern). To isolate THIS test to
the verdict-leak behavior, `_org_column` is pointed at an existing column (registry_source)
so the real `compute_org_risk_summary` code path -- including the leaking line -- runs.

Hermetic: in-memory sqlite, no network. Mirrors tests/test_freshness_never_scored_antijoin.py.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import datetime

os.environ.setdefault("DATABASE_URL", "sqlite://")
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "services" / "active" / "org_risk_summary"))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

import verdict_axis_detail_api
import logic as org_risk_summary_logic  # services/active/org_risk_summary/logic.py


def _engine_with_server():
    """One server: legacy verdict column is the worthless 'unknown', live risk_tier='MEDIUM'.
    registry_source doubles as the org proxy for the org_risk_summary test (see module docstring)."""
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(
        McpServerRegistry(
            server_id="srv1",
            name="Example MCP",
            url="https://example.test/mcp",
            registry_source="test_org",   # org proxy for site 2
            verdict="unknown",            # <-- the legacy column that must never leak
            risk_tier="MEDIUM",           # <-- the live value callers should see
            confidence=0.9,
            last_assessed=datetime(2026, 10, 9, 21, 44, 20),
        )
    )
    # verdict_axis_detail requires >=1 axis score (else 404). Labels are incidental here.
    for i, (ax, lbl) in enumerate(
        (
            ("overall_risk", "MEDIUM"),
            ("auth_strength", "STRONG"),
        ),
        start=1,
    ):
        s.add(
            McpLlmAxisScore(
                id=i,
                server_id="srv1",
                axis_name=ax,
                label=lbl,
                model_version="v1",
                scored_at=datetime(2026, 10, 9, 21, 44, 20),
            )
        )
    s.commit()
    return eng, TS


def _client(router, TS):
    app = FastAPI()
    app.include_router(router)

    def _override():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def test_verdict_axis_detail_does_not_leak_legacy_unknown():
    eng, TS = _engine_with_server()
    client = _client(verdict_axis_detail_api.router, TS)
    r = client.get("/verdict/srv1/detail")
    assert r.status_code == 200, r.text
    body = r.json()

    # GREEN: the `verdict` KEY is surfaced from the live risk_tier, not the legacy column.
    assert body["verdict"] == "MEDIUM", body
    # The dedicated risk_tier field remains present.
    assert "risk_tier" in body, body
    # RED guard: the worthless legacy 'unknown' must appear nowhere in the payload.
    assert "unknown" not in json.dumps(body).lower(), body


def test_org_risk_summary_does_not_leak_legacy_unknown(monkeypatch):
    # Point org resolution at an existing column so the real code path runs (see docstring).
    monkeypatch.setattr(
        org_risk_summary_logic,
        "_org_column",
        lambda: McpServerRegistry.registry_source,
    )
    eng, TS = _engine_with_server()
    client = _client(org_risk_summary_logic.router, TS)
    r = client.get("/api/org/test_org/risk_summary")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["total_servers"] == 1, body
    recent = body["recent_verdicts"]
    assert len(recent) == 1, body
    entry = recent[0]
    # GREEN: the `verdict` KEY is surfaced from the live risk_tier, not the legacy column.
    assert entry["verdict"] == "MEDIUM", entry
    assert entry["risk_tier"] == "MEDIUM", entry
    # RED guard: no 'unknown' leak anywhere in the payload.
    assert "unknown" not in json.dumps(body).lower(), body


if __name__ == "__main__":
    # Minimal monkeypatch shim for direct execution (no pytest).
    class _MP:
        def setattr(self, obj, name, val):
            setattr(obj, name, val)

    test_verdict_axis_detail_does_not_leak_legacy_unknown()
    test_org_risk_summary_does_not_leak_legacy_unknown(_MP())
    print("PASS")
