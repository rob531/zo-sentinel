#!/usr/bin/env python3
"""
integration_test.py -- ZO-SENTINEL End-to-End Integration Test Suite
Fixed 2026-04-22:
  - ws_query was hitting port 8773 (inference router) instead of 8772/query
  - test_mesh_events_writable wrote wrong columns; correct schema is
    agent_id, event_type, tier, payload, severity, created_at
Run: python3 integration_test.py
     python3 integration_test.py --write-db
"""
import sys, json, logging, requests
from datetime import datetime, timezone
from typing import Dict, List

log = logging.getLogger(__name__)

WRITE_SERVICE     = "http://127.0.0.1:8772"
REGISTRY_API      = "http://127.0.0.1:8781"
APPROVAL_WORKFLOW = "http://127.0.0.1:8780"

results: List[Dict] = []
WRITE_DB = False


def ws_query(sql: str) -> list:
    try:
        resp = requests.post(f"{WRITE_SERVICE}/query",
                             json={"sql": sql}, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("rows", [])
    except Exception as e:
        log.error("ws_query: %s", e)
    return []


def ws_write(table: str, rows: dict) -> bool:
    try:
        resp = requests.post(f"{WRITE_SERVICE}/write",
                             json={"table": table, "rows": rows, "wait": True},
                             timeout=15)
        return resp.status_code == 200
    except Exception as e:
        log.error("ws_write: %s", e)
        return False


def record(name: str, passed: bool, reason: str = "") -> bool:
    results.append({"name": name,
                    "status": "PASS" if passed else "FAIL",
                    "reason": reason,
                    "ts": datetime.now(timezone.utc).isoformat()})
    return passed


def print_summary():
    print("\n" + "=" * 60)
    print("ZO-SENTINEL INTEGRATION TEST RESULTS")
    print("=" * 60)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    for r in results:
        icon = "[PASS]" if r["status"] == "PASS" else "[FAIL]"
        print(f"{icon} {r['name']}" + (f": {r['reason']}" if r["reason"] else ""))
    print(f"\n{passed} passed, {failed} failed")
    print("=" * 60)
    if failed > 0:
        sys.exit(1)


# ── Individual Tests ──────────────────────────────────────────────────────────

def test_write_service_reachable():
    try:
        r = requests.get(f"{WRITE_SERVICE}/health", timeout=5)
        record("write_service_reachable", r.status_code == 200,
               f"HTTP {r.status_code}" if r.status_code != 200 else "")
    except Exception as e:
        record("write_service_reachable", False, str(e))


def test_tables_exist():
    expected = {
        "mcp_server_registry", "mcp_signal_scores", "mcp_threat_associations",
        "mcp_attestations", "mcp_decisions", "mcp_submissions", "mcp_risk_register",
        "service_health", "mesh_events", "mesh_memory", "audit_log", "auth_tokens",
    }
    try:
        rows = ws_query(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        )
        found = {r.get("table_name") for r in rows}
        missing = expected - found
        record("tables_exist",
               not missing,
               f"Missing: {sorted(missing)}" if missing else f"{len(expected)} tables OK")
    except Exception as e:
        record("tables_exist", False, str(e))


def test_servers_in_registry():
    try:
        rows = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
        cnt = rows[0].get("cnt", 0) if rows else 0
        record("servers_in_registry", cnt >= 1, f"{cnt} servers")
    except Exception as e:
        record("servers_in_registry", False, str(e))


def test_scored_servers():
    try:
        rows = ws_query(
            "SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE trust_score IS NOT NULL"
        )
        cnt = rows[0].get("cnt", 0) if rows else 0
        record("scored_servers", cnt >= 1, f"{cnt} scored servers")
    except Exception as e:
        record("scored_servers", False, str(e))


def test_registry_api_health():
    try:
        r = requests.get(f"{REGISTRY_API}/health", timeout=5)
        record("registry_api_health", r.status_code == 200,
               f"HTTP {r.status_code}" if r.status_code != 200 else "")
    except Exception as e:
        record("registry_api_health", False, str(e))


def test_registry_api_list():
    try:
        r = requests.get(f"{REGISTRY_API}/v1/registry", timeout=5)
        if r.status_code == 200:
            data = r.json()
            results_list = data.get("results", data) if isinstance(data, dict) else data
            record("registry_api_list", True, f"{len(results_list)} items")
        else:
            record("registry_api_list", False, f"HTTP {r.status_code}")
    except Exception as e:
        record("registry_api_list", False, str(e))


def test_approval_workflow_health():
    try:
        r = requests.get(f"{APPROVAL_WORKFLOW}/health", timeout=5)
        record("approval_workflow_health", r.status_code == 200,
               f"HTTP {r.status_code}" if r.status_code != 200 else "")
    except Exception as e:
        record("approval_workflow_health", False, str(e))


def test_assess_endpoint():
    try:
        r = requests.get(f"{REGISTRY_API}/v1/assess",
                         params={"mcp": "test"}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            required = ["verdict", "trust_score", "reasoning"]
            missing = [f for f in required if f not in data]
            record("assess_endpoint", not missing,
                   f"Missing fields: {missing}" if missing else f"verdict={data.get('verdict')}")
        else:
            record("assess_endpoint", False, f"HTTP {r.status_code}")
    except Exception as e:
        record("assess_endpoint", False, str(e))


def test_mesh_events_writable():
    """Write a test event using the correct mesh_events schema."""
    try:
        ok = ws_write("mesh_events", {
            "agent_id":  "integration_test",
            "event_type": "integration_test_run",
            "tier":      "T1",
            "payload":   json.dumps({"run": datetime.now(timezone.utc).isoformat()}),
            "severity":  "INFO",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        record("mesh_events_writable", ok, "" if ok else "ws_write returned False")
    except Exception as e:
        record("mesh_events_writable", False, str(e))


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    global WRITE_DB
    WRITE_DB = "--write-db" in sys.argv

    print("ZO-SENTINEL Integration Test Suite")
    print(f"  write_service:     {WRITE_SERVICE}")
    print(f"  registry_api:      {REGISTRY_API}")
    print(f"  approval_workflow: {APPROVAL_WORKFLOW}")
    print("-" * 60)

    test_write_service_reachable()
    test_tables_exist()
    test_servers_in_registry()
    test_scored_servers()
    test_registry_api_health()
    test_registry_api_list()
    test_approval_workflow_health()
    test_assess_endpoint()
    test_mesh_events_writable()

    if WRITE_DB:
        passed = sum(1 for r in results if r["status"] == "PASS")
        failed = sum(1 for r in results if r["status"] == "FAIL")
        ws_write("mesh_events", {
            "agent_id":   "integration_test",
            "event_type": "integration_test_complete",
            "tier":       "T1",
            "payload":    json.dumps({"passed": passed, "failed": failed,
                                      "results": [{"name": r["name"],
                                                   "status": r["status"],
                                                   "reason": r["reason"]}
                                                  for r in results]}),
            "severity":   "INFO" if failed == 0 else "WARNING",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    print_summary()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    run()