"""app.routers.decide_shadow: off by default, bounded, never raises, summarises honestly.

Imports the module with its one heavy dependency (verdict_breakdown_api, which pulls the
DB layer) stubbed, so this runs without Postgres or Clerk.
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def shadow(monkeypatch):
    stub = types.ModuleType("verdict_breakdown_api")

    class Principal:
        def __init__(self, role="public"):
            self.role, self.user_id = role, "u"

    stub.Principal = Principal
    stub.get_principal = lambda: Principal()
    stub.require_admin = lambda: Principal("admin")
    monkeypatch.setitem(sys.modules, "verdict_breakdown_api", stub)
    sys.modules.pop("app.routers.decide_shadow", None)
    from app.routers import decide_shadow as m
    m._RECORDS.clear()
    m._WINDOW.update(start=0.0, count=0)
    for k in ("DECIDE_SHADOW", "DECIDE_SHADOW_BACKEND", "OPENROUTER_API_KEY", "FASTINO_API_KEY",
              "DECIDE_SHADOW_MAX_PER_HOUR"):
        monkeypatch.delenv(k, raising=False)
    return m


class _BG:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *a):
        self.tasks.append((fn, a))


def test_off_by_default_even_with_a_key(shadow, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    bg = _BG()
    out = shadow.decide_shadow(shadow.ShadowIn(q="critical servers", actual="explore"), bg, shadow.Principal())
    assert out == {"shadow": "off"} and bg.tasks == []


def test_flag_without_key_stays_off(shadow, monkeypatch):
    monkeypatch.setenv("DECIDE_SHADOW", "1")
    assert shadow.shadow_enabled() is False
    monkeypatch.setenv("DECIDE_SHADOW_BACKEND", "glide")
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")  # wrong key for glide
    assert shadow.shadow_enabled() is False


def test_hourly_budget_caps_queued_work(shadow, monkeypatch):
    monkeypatch.setenv("DECIDE_SHADOW", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setenv("DECIDE_SHADOW_MAX_PER_HOUR", "2")
    bg = _BG()
    res = [shadow.decide_shadow(shadow.ShadowIn(q="q", actual="explore"), bg, shadow.Principal())["shadow"]
           for _ in range(3)]
    assert res == ["queued", "queued", "budget"] and len(bg.tasks) == 2


def test_run_records_an_error_instead_of_raising(shadow, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")  # JevBackend will refuse -> DecisionError
    shadow._run(shadow.ShadowIn(q="anything", actual="explore"), "public")
    rec = shadow._RECORDS[-1]
    assert rec["error"] == "DecisionError" and rec["actual"] == "explore"


def test_run_logs_choice_and_match_with_a_fake_backend(shadow, monkeypatch):
    import zo_sentinel.decide.backends as b
    from zo_sentinel.decide.ui_compose import VIEWS, RISK

    def fake(self, state, plan):
        raw = {}
        for qid, (q, order) in plan.items():
            keys = q.keys()
            pick = {"view": "explore", "risk": "CRITICAL"}.get(qid, "false")
            raw[qid] = {k: (1.0 if k == pick else 0.0) for k in keys}
        return raw, {"calls": 1}

    monkeypatch.setattr(b.JevBackend, "__call__", fake)
    assert "explore" in VIEWS and "CRITICAL" in RISK
    shadow._run(shadow.ShadowIn(q="most dangerous servers", actual="explore", risk="CRITICAL"), "public")
    rec = shadow._RECORDS[-1]
    assert "error" not in rec
    assert rec["view"] == "explore" and rec["view_match"] is True and rec["risk_match"] is True


def test_summary_reports_agreement_and_errors(shadow):
    recs = [{"view": "explore", "view_match": True, "fallback": False, "elapsed_ms": 100},
            {"view": "dashboard", "view_match": False, "fallback": False, "elapsed_ms": 300},
            {"error": "TimeoutError", "elapsed_ms": 5000}]
    s = shadow.summarise(recs)
    assert s["n"] == 3 and s["errors"] == 1 and s["view_agreement"] == 0.5
    assert s["would_route_to"] == {"dashboard": 1, "explore": 1} and s["elapsed_ms_p50"] == 200


def test_query_length_is_bounded(shadow):
    with pytest.raises(Exception):
        shadow.ShadowIn(q="x" * 301, actual="explore")


def test_routes_live_where_the_page_calls_them(shadow):
    # The spine's include_router() applies no prefix, so the router must carry /api itself;
    # app.html POSTs to /api/decide/shadow. A bare APIRouter() mounted these at /decide/...
    paths = {r.path for r in shadow.router.routes}
    assert "/api/decide/shadow" in paths and "/api/admin/decide/shadow" in paths
    assert "fetch('/api/decide/shadow'" in (ROOT / "app" / "static" / "app.html").read_text(encoding="utf-8")


def test_direct_typesafe_key_enables_when_base_url_points_there(shadow, monkeypatch):
    # Prod uses the direct TypeSafe endpoint; the switch must look for the key the backend will use.
    monkeypatch.setenv("DECIDE_SHADOW", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "x")
    assert shadow.shadow_enabled() is False          # default endpoint is OpenRouter
    monkeypatch.setenv("JEV_BASE_URL", "https://api.typesafe.ai/v1/systemone")
    assert shadow.shadow_enabled() is True
    import zo_sentinel.decide.backends as b
    assert b.JevBackend().key_env == "TYPESAFE_API_KEY"   # and the backend agrees
