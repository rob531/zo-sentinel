"""App functional E2E -- the pre/post-cutover baseline.

Stands up against the write_service HTTP contract (/execute,/write,/query) -- the
exact DB-access seam the DuckDB->Postgres migration swaps (docs/POSTGRES_APP_MIGRATION_SCOPE.md)
-- and snapshots the data-layer behaviour + app-API presence so the SAME run can be
diffed across the cutover:

    pre-cutover : IT_DB_BACKEND=duckdb  (default, today)
    post-cutover: IT_DB_BACKEND=postgres (it_write_service gains the pg branch +
                  a `services: postgres:16` container, same as the integration tier)

The snapshot artifacts/app_e2e_snapshot_<backend>.json is uploaded by e2e-nightly.yml.
Diff the duckdb vs postgres snapshots to prove the migration preserves app behaviour
(the app-level complement to the scope doc's row-count/checksum verify).

Backend-agnostic + collision-free: self-creates e2e_* tables with Postgres-portable DDL
(no AUTOINCREMENT/STRUCT), so it needs no host schema and never touches product tables.
Runs nightly (.github/workflows/e2e-nightly.yml); needs it_write_service up.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

WS = os.environ.get("ZO_WRITE_SERVICE", "http://127.0.0.1:8772")
BACKEND = os.environ.get("IT_DB_BACKEND", "duckdb")
ART = REPO / "artifacts"
ART.mkdir(exist_ok=True)
SNAP = ART / f"app_e2e_snapshot_{BACKEND}.json"

AXES = ["overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface"]
# Deterministic seed: (server_id, composite) -> a known verdict tier each.
SEED = [("srv0", 90), ("srv1", 65), ("srv2", 50), ("srv3", 35), ("srv4", 20), ("srv5", 10)]


def _tier(score: float) -> str:
    return ("TRUSTED_GENERAL" if score > 75 else
            "TRUSTED_RESEARCH" if score > 60 else
            "ENTERPRISE_CONTROLLED" if score > 45 else
            "CAUTION_LIMITED" if score > 30 else
            "HIGH_RISK_ISOLATED" if score > 15 else "KNOWN_THREAT")


def _exec(sql: str):
    return requests.post(f"{WS}/execute", json={"sql": sql}, timeout=10)


def _write(table: str, row: dict):
    return requests.post(f"{WS}/write", json={"table": table, "rows": [row], "wait": True}, timeout=10)


def _query(sql: str) -> list:
    r = requests.post(f"{WS}/query", json={"sql": sql}, timeout=10)
    d = r.json()
    return d.get("rows", []) if isinstance(d, dict) else (d or [])


def _ws_up() -> bool:
    try:
        return requests.get(f"{WS}/health", timeout=3).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _ws_up(), reason="write_service not reachable (start it_write_service / set ZO_WRITE_SERVICE)")


def _seed():
    _exec("DROP TABLE IF EXISTS e2e_servers")
    _exec("DROP TABLE IF EXISTS e2e_axis_scores")
    _exec("CREATE TABLE e2e_servers (server_id VARCHAR PRIMARY KEY, mcp_name VARCHAR, "
          "composite DOUBLE PRECISION, risk_tier VARCHAR)")
    _exec("CREATE TABLE e2e_axis_scores (server_id VARCHAR, axis_name VARCHAR, label VARCHAR, "
          "label_index INTEGER, p_top DOUBLE PRECISION, model_version VARCHAR, "
          "PRIMARY KEY (server_id, axis_name, model_version))")
    for sid, comp in SEED:
        _write("e2e_servers", {"server_id": sid, "mcp_name": f"mcp-{sid}",
                               "composite": comp, "risk_tier": _tier(comp)})
        for ax in AXES:
            _write("e2e_axis_scores", {"server_id": sid, "axis_name": ax, "label": "MEDIUM",
                                       "label_index": 1, "p_top": 0.7, "model_version": "v3.0"})


def _app_api_probe() -> dict:
    """Presence/importability of the app-foundation API modules as the builder lands them."""
    import importlib
    out = {}
    for mod in ("verdict_breakdown_api", "overview_dashboard_api", "org_entity_search_api",
                "entity_report_exporter"):
        if not (REPO / f"{mod}.py").exists():
            out[mod] = "not-built"
            continue
        try:
            m = importlib.import_module(mod)
            out[mod] = "importable" if (getattr(m, "app", None) or getattr(m, "router", None)
                                        or any(callable(getattr(m, a, None)) for a in dir(m))) else "no-app"
        except Exception as e:
            out[mod] = f"import-error: {type(e).__name__}"
    return out


def _snapshot() -> dict:
    return {
        "backend": BACKEND,
        # data-layer reads -- the migration MUST preserve these byte-for-byte
        "servers": sorted(_query("SELECT server_id, mcp_name, composite, risk_tier FROM e2e_servers"),
                          key=lambda r: r["server_id"]),
        "verdict_breakdown_srv0": sorted(
            _query("SELECT axis_name, label, p_top FROM e2e_axis_scores WHERE server_id='srv0'"),
            key=lambda r: r["axis_name"]),
        "tier_distribution": sorted(
            _query("SELECT risk_tier, COUNT(*) AS n FROM e2e_servers GROUP BY risk_tier"),
            key=lambda r: r["risk_tier"]),
        "high_risk_servers": sorted(
            _query("SELECT server_id FROM e2e_servers WHERE composite <= 30"),
            key=lambda r: r["server_id"]),
        # product app-API surface presence (grows as the builder lands modules)
        "app_apis": _app_api_probe(),
    }


def test_app_functional_e2e_snapshot():
    _seed()
    snap = _snapshot()
    SNAP.write_text(json.dumps(snap, indent=2, sort_keys=True, default=str), encoding="utf-8")
    # invariants that MUST hold on both backends (== the cutover acceptance):
    assert len(snap["servers"]) == len(SEED)
    assert sum(int(r["n"]) for r in snap["tier_distribution"]) == len(SEED)
    assert len(snap["verdict_breakdown_srv0"]) == len(AXES)
    assert snap["servers"][0]["risk_tier"] == "TRUSTED_GENERAL"
    assert [r["server_id"] for r in snap["high_risk_servers"]] == ["srv4", "srv5"]
    print(f"app E2E snapshot written: {SNAP} (backend={BACKEND})")


