"""Regression tests for the 2026-11-24 prod infra perf & RBAC fixes.

Verifies:
1. facet_enum_service.cached_facets() serves aged cache (> STALE_MAX_SECS) and
   cold-hydrates from app_stats without synchronous compute_facets() full scans.
2. verdict_breakdown_api.search_servers() uses EXISTS + PK ordering + O(1) batched
   score lookup (no IN (SELECT...), no lower(name) sort, no N+1 _hit queries),
   and honors trust_gating_override + reveal=False.
3. verdict_breakdown_api.top_servers() uses O(1) batched score lookup (no N+1).
4. mcp_exemptions_dashboard_view.html gates /api/admin/* calls & Admin nav link on
   role === 'admin', and scan_view.html clears #status when loading the example.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("CLERK_PUBLISHABLE_KEY", "")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import McpLlmAxisScore, McpServerRegistry
import facet_enum_service as fes
import verdict_breakdown_api as vapi

REPO = pathlib.Path(__file__).resolve().parents[1]


def _make_session_factory():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS app_stats ("
                "key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS api_usage ("
                "user_id TEXT NOT NULL, day DATE NOT NULL, "
                "lookups INTEGER NOT NULL DEFAULT 0, "
                "PRIMARY KEY (user_id, day))"
            )
        )
    return eng, sessionmaker(bind=eng, autoflush=False, autocommit=False)


def test_facets_unconditional_swr_and_cold_app_stats_hydrate(monkeypatch):
    eng, LocalS = _make_session_factory()
    s = LocalS()
    try:
        # 1) Warm RAM cache past STALE_MAX_SECS must still serve immediately (no sync scan)
        fes._CACHE["facets"] = {"risk_tier": [{"value": "HIGH", "count": 42}]}
        fes._CACHE["at"] = time.time() - (fes.STALE_MAX_SECS + 120)

        calls = {"n": 0}
        refreshes = []

        def _boom(_db):
            calls["n"] += 1
            raise AssertionError("compute_facets must not run synchronously when cache is warm")

        monkeypatch.setattr(fes, "compute_facets", _boom)
        monkeypatch.setattr(fes, "_trigger_bg_refresh", lambda session_factory=None: refreshes.append(True))

        got = fes.cached_facets(s, session_factory=LocalS)
        assert got["risk_tier"][0]["count"] == 42
        assert calls["n"] == 0
        assert len(refreshes) == 1

        # 2) Cold RAM cache hydrates from persisted app_stats without compute_facets
        fes._CACHE["facets"] = None
        fes._CACHE["at"] = 0.0
        persisted = {"risk_tier": [{"value": "LOW", "count": 99}]}
        s.execute(
            text(
                "INSERT INTO app_stats(key, value, updated_at) "
                "VALUES (:k, :v, :ts)"
            ),
            {
                "k": fes.FACETS_STATS_KEY,
                "v": json.dumps(persisted),
                "ts": "2020-01-01T00:00:00+00:00",
            },
        )
        s.commit()

        refreshes.clear()
        got_cold = fes.cached_facets(s, session_factory=LocalS)
        assert got_cold["risk_tier"][0]["count"] == 99
        assert calls["n"] == 0
        assert len(refreshes) == 1
    finally:
        fes._CACHE["facets"] = None
        fes._CACHE["at"] = 0.0
        s.close()
        eng.dispose()


def test_facets_periodic_refresher_starts_and_stops_cleanly():
    eng, LocalS = _make_session_factory()
    s = LocalS()
    try:
        s.add(McpServerRegistry(server_id="f1", risk_tier="HIGH", verdict="HIGH", registry_source="github", trust_score=20.0))
        s.commit()
        fes._CACHE["facets"] = None
        fes._CACHE["at"] = 0.0
        stop = fes.start_periodic_refresher(interval_secs=3600, session_factory=LocalS)
        try:
            deadline = time.time() + 3.0
            while fes._CACHE["facets"] is None and time.time() < deadline:
                time.sleep(0.02)
            assert fes._CACHE["facets"] is not None
        finally:
            stop.set()
    finally:
        fes._CACHE["facets"] = None
        fes._CACHE["at"] = 0.0
        s.close()
        eng.dispose()


def test_search_servers_uses_exists_pk_order_batched_hits_and_trust_gate():
    eng, LocalS = _make_session_factory()
    s = LocalS()
    try:
        sid_score = 1
        # 12 untrusted 'mcp' servers scored HIGH
        for i in range(12):
            sid = f"srv_{i:02d}"
            s.add(McpServerRegistry(
                server_id=sid,
                name=f"Zulu {i} mcp server",
                url=f"https://github.com/rando{i}/mcp-tool",
                registry_source="github",
            ))
            s.add(McpLlmAxisScore(id=sid_score, server_id=sid, axis_name="overall_risk", label="HIGH", model_version="v3"))
            sid_score += 1
            s.add(McpLlmAxisScore(id=sid_score, server_id=sid, axis_name="maintainer_trust", label="UNKNOWN_AUTHOR", model_version="v3"))
            sid_score += 1

        # 1 trusted Stripe 'mcp' server scored raw HIGH -> trust_gate caps to MEDIUM -> excluded by risk=HIGH
        s.add(McpServerRegistry(
            server_id="srv_stripe",
            name="Alpha Stripe mcp",
            url="https://github.com/stripe/agent-toolkit",
            registry_source="github",
        ))
        s.add(McpLlmAxisScore(id=sid_score, server_id="srv_stripe", axis_name="overall_risk", label="HIGH", model_version="v3"))
        sid_score += 1
        s.add(McpLlmAxisScore(id=sid_score, server_id="srv_stripe", axis_name="maintainer_trust", label="ESTABLISHED", model_version="v3"))
        s.commit()

        sql_log = []

        def _capture(_conn, _cursor, statement, _params, _ctx, _many):
            sql_log.append(statement)

        event.listen(eng, "before_cursor_execute", _capture)

        app = FastAPI()
        app.include_router(vapi.router)

        def _override_db():
            d = LocalS()
            try:
                yield d
            finally:
                d.close()

        role_box = {"role": "insider"}
        app.dependency_overrides[vapi.get_session] = _override_db
        app.dependency_overrides[vapi.get_principal] = lambda: vapi.Principal(user_id="u1", role=role_box["role"])
        c = TestClient(app)

        sql_log.clear()
        r = c.get("/api/servers?q=mcp&risk=HIGH&limit=10")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["reveal"] is True
        assert body["count"] == 10
        returned_ids = [x["server_id"] for x in body["servers"]]
        assert "srv_stripe" not in returned_ids
        assert all(x["published_overall_risk"] == "HIGH" for x in body["servers"])

        # Assert SQL shape & O(1) query count (no N+1, no lower(name) sort, no IN (SELECT))
        assert len(sql_log) <= 3, f"Expected <=3 SQL statements, got {len(sql_log)}: {sql_log}"
        search_sql = next(st for st in sql_log if "FROM mcp_server_registry" in st)
        upper_sql = search_sql.upper()
        assert "EXISTS" in upper_sql, f"Expected EXISTS predicate: {search_sql}"
        assert "IN (SELECT" not in upper_sql, f"Unexpected IN (SELECT): {search_sql}"
        assert "ORDER BY LOWER(" not in upper_sql, f"Unexpected lower(name) sort: {search_sql}"
        assert "ORDER BY MCP_SERVER_REGISTRY.SERVER_ID" in upper_sql, f"Expected PK sort: {search_sql}"

        # Public role hides risk tiers without issuing score queries
        role_box["role"] = "public"
        sql_log.clear()
        rp = c.get("/api/servers?q=mcp&risk=HIGH&limit=5")
        assert rp.status_code == 200, rp.text
        pj = rp.json()
        assert pj["reveal"] is False
        assert all(x["hidden"] is True and x["published_overall_risk"] is None for x in pj["servers"])
        assert not any("mcp_llm_axis_scores" in st.lower() for st in sql_log)

        # top_servers also uses O(1) batched score lookup (no N+1 across candidates)
        role_box["role"] = "insider"
        sql_log.clear()
        rt = c.get("/api/top?risk=HIGH&limit=5")
        assert rt.status_code == 200, rt.text
        tj = rt.json()
        assert len(tj["servers"]) == 5
        assert len(sql_log) <= 3, f"Expected <=3 SQL statements in top_servers, got {len(sql_log)}"
    finally:
        s.close()
        eng.dispose()


def test_html_admin_rbac_gate_and_scan_status_clear():
    exemptions_html = (REPO / "mcp_exemptions_dashboard_view.html").read_text(encoding="utf-8")
    assert 'id="navAdmin" style="display:none"' in exemptions_html
    assert "api('/api/me')" in exemptions_html
    assert "me.role==='admin'" in exemptions_html
    assert "function boot(){loadWatchlist();loadAdmin();}" not in exemptions_html

    scan_html = (REPO / "scan_view.html").read_text(encoding="utf-8")
    ex_idx = scan_html.index("$('ex').onclick")
    snippet = scan_html[ex_idx:ex_idx + 240]
    assert "st.classList.remove('err')" in snippet
    assert "st.textContent=''" in snippet
