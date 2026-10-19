import time
import uuid
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

WRITE_SERVICE = "http://127.0.0.1:8772/write"
QUERY_SERVICE = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE = "http://127.0.0.1:8772/execute"

LOG_FILE = "/home/workspace/zo_sentinel/e2e_scenarios_run.log"
SERVICE_NAME = "e2e_scenarios_run"

# Tables confirmed in real bus schema:
# mcp_server_registry: server_id, name, url, description, trust_score, verdict, registry_source, scan_count
# mcp_signal_scores: server_id, signal_name, score, evidence, scored_at
# audit_log: event_id, event_type, actor, action, target_server_id, details_json, outcome, timestamp, immutable
# service_health: service, last_heartbeat


def log(msg: str):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as fh:
        fh.write(line + "\n")


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    import requests
    resp = requests.post(WRITE_SERVICE, json={"table": table, "rows": rows}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> Dict[str, Any]:
    import requests
    resp = requests.post(QUERY_SERVICE, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql: str) -> Dict[str, Any]:
    import requests
    resp = requests.post(EXECUTE_SERVICE, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def verify_table_exists(table_name: str) -> bool:
    result = ws_query(f"SELECT count(*) as cnt FROM information_schema.tables WHERE table_name = '{table_name}'")
    return result.get("rows", [{}])[0].get("cnt", 0) > 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def deterministic_id(prefix: str, *fields: str) -> str:
    import hashlib
    raw = "|".join(fields)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def clear_test_artifacts(flow_id: str):
    """Remove test servers created by this flow."""
    ws_execute(f"DELETE FROM mcp_server_registry WHERE server_id LIKE 'srv-e2e-{flow_id}%'")
    log(f"[{flow_id}] Cleaned up test artifacts")


def run_flow_1_new_mcp_cycle() -> Dict[str, Any]:
    """
    Flow 1: new MCP → signal scored → verdict reflected in registry → audit trail.
    Tests the core write path: mcp_server_registry + mcp_signal_scores + audit_log.
    """
    log("=" * 60)
    log("FLOW 1: New MCP → Signal Scored → Registry Update → Audit Trail")
    log("=" * 60)

    flow_id = f"e2e-{uuid.uuid4().hex[:8]}"
    server_id = f"srv-e2e-{flow_id}-{uuid.uuid4().hex[:8]}"
    now = utc_now_iso()

    results = {"flow_id": flow_id, "steps": [], "passed": True}

    # Step 1: Verify required tables exist
    log(f"[{flow_id}] Step 1: Verify schema tables exist")
    for tbl in ["mcp_server_registry", "mcp_signal_scores", "audit_log", "service_health"]:
        exists = verify_table_exists(tbl)
        results["steps"].append({"step": f"table_exists_{tbl}", "passed": exists})
        if not exists:
            log(f"[{flow_id}] FAIL: table {tbl} does not exist")
            results["passed"] = False
            return results
    log(f"[{flow_id}] All required tables verified")

    # Step 2: Register a new MCP server
    log(f"[{flow_id}] Step 2: Register MCP server {server_id}")
    try:
        ws_write("mcp_server_registry", [{
            "server_id": server_id,
            "name": f"e2e-test-server-{flow_id}",
            "url": f"https://example.com/e2e/{flow_id}",
            "description": "E2E test server - safe for deletion",
            "trust_score": None,
            "verdict": "unknown",
            "registry_source": "e2e_test",
            "scan_count": 0
        }])
        results["steps"].append({"step": "register_server", "passed": True})
        log(f"[{flow_id}] Server registered: {server_id}")
    except Exception as ex:
        log(f"[{flow_id}] FAIL: Could not register server: {ex}")
        results["steps"].append({"step": "register_server", "passed": False, "error": str(ex)})
        results["passed"] = False
        return results

    # Step 3: Write signal scores for this server
    log(f"[{flow_id}] Step 3: Write signal scores for {server_id}")
    signals = [
        {"server_id": server_id, "signal_name": "community_trust", "score": 0.75, "evidence": json.dumps({"stars": 120, "downloads": 5000}), "scored_at": now},
        {"server_id": server_id, "signal_name": "supply_chain", "score": 0.85, "evidence": json.dumps({"age_days": 365, "publisher_verified": True}), "scored_at": now},
        {"server_id": server_id, "signal_name": "temporal_stability", "score": 0.90, "evidence": json.dumps({"version_count": 5, "consistent_updates": True}), "scored_at": now},
    ]
    try:
        ws_write("mcp_signal_scores", signals)
        results["steps"].append({"step": "write_signal_scores", "passed": True})
        log(f"[{flow_id}] Signal scores written: {len(signals)} signals")
    except Exception as ex:
        log(f"[{flow_id}] FAIL: Could not write signal scores: {ex}")
        results["steps"].append({"step": "write_signal_scores", "passed": False, "error": str(ex)})
        results["passed"] = False
        clear_test_artifacts(flow_id)
        return results

    # Step 4: Query signal scores back
    log(f"[{flow_id}] Step 4: Query signal scores for {server_id}")
    try:
        scored = ws_query(f"SELECT signal_name, score FROM mcp_signal_scores WHERE server_id = '{server_id}'")
        rows = scored.get("rows", [])
        if len(rows) >= 3:
            results["steps"].append({"step": "query_signal_scores", "passed": True, "count": len(rows)})
            log(f"[{flow_id}] Signal scores retrieved: {len(rows)} rows")
        else:
            results["steps"].append({"step": "query_signal_scores", "passed": False, "count": len(rows)})
            results["passed"] = False
    except Exception as ex:
        log(f"[{flow_id}] FAIL: Could not query signal scores: {ex}")
        results["steps"].append({"step": "query_signal_scores", "passed": False, "error": str(ex)})
        results["passed"] = False

    # Step 5: Update registry with computed trust score
    log(f"[{flow_id}] Step 5: Update registry trust_score for {server_id}")
    avg_score = sum(s["score"] for s in signals) / len(signals)
    try:
        ws_execute(f"UPDATE mcp_server_registry SET trust_score = {avg_score:.4f}, verdict = 'trusted_research', scan_count = scan_count + 1 WHERE server_id = '{server_id}'")
        results["steps"].append({"step": "update_registry", "passed": True, "trust_score": avg_score})
        log(f"[{flow_id}] Registry updated with trust_score={avg_score:.4f}")
    except Exception as ex:
        log(f"[{flow_id}] FAIL: Could not update registry: {ex}")
        results["steps"].append({"step": "update_registry", "passed": False, "error": str(ex)})
        results["passed"] = False

    # Step 6: Write audit trail entry
    log(f"[{flow_id}] Step 6: Write audit log entry")
    event_id = deterministic_id("audit", flow_id, server_id, now)
    try:
        ws_write("audit_log", [{
            "event_id": event_id,
            "event_type": "e2e_flow_1",
            "actor": "e2e_scenarios_run",
            "action": "test_new_mcp_cycle",
            "target_server_id": server_id,
            "details_json": json.dumps({"flow_id": flow_id, "signals_written": len(signals), "trust_score": avg_score}),
            "outcome": "success",
            "timestamp": now,
            "immutable": True
        }])
        results["steps"].append({"step": "write_audit_log", "passed": True})
        log(f"[{flow_id}] Audit log written: {event_id}")
    except Exception as ex:
        log(f"[{flow_id}] FAIL: Could not write audit log: {ex}")
        results["steps"].append({"step": "write_audit_log", "passed": False, "error": str(ex)})
        results["passed"] = False

    # Step 7: Verify end-to-end read path
    log(f"[{flow_id}] Step 7: End-to-end verification - registry + signals joined")
    try:
        joined = ws_query(f"""
            SELECT r.server_id, r.name, r.verdict, r.trust_score,
                   COUNT(s.signal_name) as signal_count,
                   AVG(s.score) as avg_signal_score
            FROM mcp_server_registry r
            LEFT JOIN mcp_signal_scores s ON r.server_id = s.server_id
            WHERE r.server_id = '{server_id}'
            GROUP BY r.server_id, r.name, r.verdict, r.trust_score
        """)
        rows = joined.get("rows", [])
        if rows:
            row = rows[0]
            verdict_ok = row.get("verdict") == "trusted_research"
            score_ok = row.get("avg_signal_score") is not None and row.get("avg_signal_score") > 0
            results["steps"].append({
                "step": "e2e_verification",
                "passed": verdict_ok and score_ok,
                "verdict": row.get("verdict"),
                "signal_count": row.get("signal_count"),
                "avg_score": row.get("avg_signal_score")
            })
            log(f"[{flow_id}] E2E verified: verdict={row.get('verdict')}, signals={row.get('signal_count')}, avg_score={row.get('avg_signal_score')}")
            if not (verdict_ok and score_ok):
                results["passed"] = False
        else:
            results["steps"].append({"step": "e2e_verification", "passed": False, "reason": "no rows returned"})
            results["passed"] = False
    except Exception as ex:
        log(f"[{flow_id}] FAIL: E2E verification failed: {ex}")
        results["steps"].append({"step": "e2e_verification", "passed": False, "error": str(ex)})
        results["passed"] = False

    # Cleanup
    clear_test_artifacts(flow_id)
    log(f"[{flow_id}] Flow 1 complete. passed={results['passed']}")
    return results


def run_flow_2_bulk_write_cycle() -> Dict[str, Any]:
    """
    Flow 2: Bulk write many servers and signals, verify throughput.
    Tests write_service batch efficiency and signal score cardinality.
    """
    log("=" * 60)
    log("FLOW 2: Bulk Write Cycle - Many Servers and Signals")
    log("=" * 60)

    flow_id = f"e2e-{uuid.uuid4().hex[:8]}"
    now = utc_now_iso()
    batch_size = 20

    results = {"flow_id": flow_id, "steps": [], "passed": True}

    # Step 1: Bulk register servers
    log(f"[{flow_id}] Step 1: Bulk register {batch_size} servers")
    servers = [
        {
            "server_id": f"srv-e2e-{flow_id}-{i:03d}",
            "name": f"bulk-test-server-{flow_id}-{i:03d}",
            "url": f"https://example.com/bulk/{flow_id}/{i}",
            "description": f"E2E bulk test server {i}",
            "trust_score": None,
            "verdict": "unknown",
            "registry_source": "e2e_bulk_test",
            "scan_count": 0
        }
        for i in range(batch_size)
    ]
    try:
        ws_write("mcp_server_registry", servers)
        results["steps"].append({"step": "bulk_register", "passed": True, "count": batch_size})
        log(f"[{flow_id}] Bulk registered {batch_size} servers")
    except Exception as ex:
        log(f"[{flow_id}] FAIL: Bulk register failed: {ex}")
        results["steps"].append({"step": "bulk_register", "passed": False, "error": str(ex)})
        results["passed"] = False
        return results

    # Step 2: Bulk write signal scores
    log(f"[{flow_id}] Step 2: Bulk write signal scores for {batch_size} servers")
    signals = []
    signal_types = ["community_trust", "supply_chain", "temporal_stability", "permission_scope", "tool_description_safety", "context_efficiency"]
    for i in range(batch_size):
        for sig_name in signal_types:
            signals.append({
                "server_id": f"srv-e2e-{flow_id}-{i:03d}",
                "signal_name": sig_name,
                "score": 0.5 + (i % 10) * 0.04,
                "evidence": json.dumps({"batch_index": i, "signal_type": sig_name}),
                "scored_at": now
            })
    try:
        ws_write("mcp_signal_scores", signals)
        results["steps"].append({"step": "bulk_write_signals", "passed": True, "count": len(signals)})
        log(f"[{flow_id}] Bulk wrote {len(signals)} signal scores")
    except Exception as ex:
        log(f"[{flow_id}] FAIL: Bulk write signals failed: {ex}")
        results["steps"].append({"step": "bulk_write_signals", "passed": False, "error": str(ex)})
        results["passed"] = False
        clear_test_artifacts(flow_id)
        return results

    # Step 3: Verify cardinality - each server should have all 6 signal types
    log(f"[{flow_id}] Step 3: Verify signal cardinality across servers")
    try:
        cardinality = ws_query(f"""
            SELECT server_id, COUNT(DISTINCT signal_name) as distinct_signals,
                   COUNT(*) as total_rows
            FROM mcp_signal_scores
            WHERE server_id LIKE 'srv-e2e-{flow_id}%'
            GROUP BY server_id
        """)
        rows = cardinality.get("rows", [])
        all_have_all = all(r.get("distinct_signals", 0) >= 6 for r in rows)
        results["steps"].append({
            "step": "verify_cardinality",
            "passed": all_have_all and len(rows) == batch_size,
            "servers_scored": len(rows),
            "expected_servers": batch_size
        })
        log(f"[{flow_id}] Cardinality check: {len(rows)} servers scored, all >= 6 signals: {all_have_all}")
        if not all_have_all or len(rows) != batch_size:
            results["passed"] = False
    except Exception as ex:
        log(f"[{flow_id}] FAIL: Cardinality check failed: {ex}")
        results["steps"].append({"step": "verify_cardinality", "passed": False, "error": str(ex)})
        results["passed"] = False

    # Step 4: Query by verdict filter
    log(f"[{flow_id}] Step 4: Query filtered by verdict = unknown (bulk servers)")
    try:
        filtered = ws_query(f"SELECT count(*) as cnt FROM mcp_server_registry WHERE server_id LIKE 'srv-e2e-{flow_id}%' AND verdict = 'unknown'")
        count = filtered.get("rows", [{}])[0].get("cnt", 0)
        ok = count == batch_size
        results["steps"].append({"step": "filter_by_verdict", "passed": ok, "count": count, "expected": batch_size})
        log(f"[{flow_id}] Filtered verdict=unknown: {count} rows")
        if not ok:
            results["passed"] = False
    except Exception as ex:
        log(f"[{flow_id}] FAIL: Filter by verdict failed: {ex}")
        results["steps"].append({"step": "filter_by_verdict", "passed": False, "error": str(ex)})
        results["passed"] = False

    # Cleanup
    clear_test_artifacts(flow_id)
    log(f"[{flow_id}] Flow 2 complete. passed={results['passed']}")
    return results


def run_flow_3_service_health_cycle() -> Dict[str, Any]:
    """
    Flow 3: Verify service_health table, write a heartbeat, query it back.
    """
    log("=" * 60)
    log("FLOW 3: Service Health Heartbeat Cycle")
    log("=" * 60)

    flow_id = f"e2e-{uuid.uuid4().hex[:8]}"
    now = utc_now_iso()

    results = {"flow_id": flow_id, "steps": [], "passed": True}
    service_name = f"e2e_test_heartbeat_{flow_id}"

    # Step 1: Write heartbeat
    log(f"[{flow_id}] Step 1: Write heartbeat for {service_name}")
    try:
        ws_write("service_health", [{
            "service": service_name,
            "last_heartbeat": now
        }])
        results["steps"].append({"step": "write_heartbeat", "passed": True})
        log(f"[{flow_id}] Heartbeat written for {service_name}")
    except Exception as ex:
        log(f"[{flow_id}] FAIL: Write heartbeat failed: {ex}")
        results["steps"].append({"step": "write_heartbeat", "passed": False, "error": str(ex)})
        results["passed"] = False
        return results

    # Step 2: Query heartbeat back
    log(f"[{flow_id}] Step 2: Query heartbeat for {service_name}")
    try:
        hb = ws_query(f"SELECT service, last_heartbeat FROM service_health WHERE service = '{service_name}'")
        rows = hb.get("rows", [])
        if rows:
            results["steps"].append({"step": "query_heartbeat", "passed": True, "heartbeat": rows[0].get("last_heartbeat")})
            log(f"[{flow_id}] Heartbeat retrieved: {rows[0].get('last_heartbeat')}")
        else:
            results["steps"].append({"step": "query_heartbeat", "passed": False})
            results["passed"] = False
    except Exception as ex:
        log(f"[{flow_id}] FAIL: Query heartbeat failed: {ex}")
        results["steps"].append({"step": "query_heartbeat", "passed": False, "error": str(ex)})
        results["passed"] = False

    # Step 3: Overwrite with newer heartbeat (upsert-style via DELETE+INSERT or UPDATE)
    log(f"[{flow_id}] Step 3: Overwrite heartbeat with newer timestamp")
    now2 = utc_now_iso()
    try:
        ws_execute(f"UPDATE service_health SET last_heartbeat = '{now2}' WHERE service = '{service_name}'")
        results["steps"].append({"step": "update_heartbeat", "passed": True})
        log(f"[{flow_id}] Heartbeat updated")
    except Exception as ex:
        log(f"[{flow_id}] FAIL: Update heartbeat failed: {ex}")
        results["steps"].append({"step": "update_heartbeat", "passed": False, "error": str(ex)})
        results["passed"] = False

    # Cleanup: remove test heartbeat
    try:
        ws_execute(f"DELETE FROM service_health WHERE service = '{service_name}'")
    except Exception:
        pass

    log(f"[{flow_id}] Flow 3 complete. passed={results['passed']}")
    return results


def send_heartbeat():
    """Write a heartbeat for this e2e runner itself."""
    now = utc_now_iso()
    ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": now}])


def run_all_flows() -> Dict[str, Any]:
    """Execute all E2E flows and return aggregated results."""
    log("=" * 70)
    log("E2E SCENARIOS RUN - Starting all flows")
    log("=" * 70)

    flows = [
        ("flow_1_new_mcp_cycle", run_flow_1_new_mcp_cycle),
        ("flow_2_bulk_write_cycle", run_flow_2_bulk_write_cycle),
        ("flow_3_service_health_cycle", run_flow_3_service_health_cycle),
    ]

    results = {"flows": [], "total_passed": True, "timestamp": utc_now_iso()}

    for name, fn in flows:
        try:
            r = fn()
            results["flows"].append(r)
            if not r.get("passed"):
                results["total_passed"] = False
        except Exception as ex:
            log(f"[E2E] Unhandled exception in {name}: {ex}")
            results["flows"].append({"flow": name, "passed": False, "error": str(ex)})
            results["total_passed"] = False

    log("=" * 70)
    log("E2E SCENARIOS RUN - Summary")
    log("=" * 70)
    for r in results["flows"]:
        status = "PASS" if r.get("passed") else "FAIL"
        log(f"  [{status}] {r.get('flow_id', 'unknown')}")
    log(f"  Overall: {'ALL PASSED' if results['total_passed'] else 'SOME FAILED'}")
    log("=" * 70)

    return results


def check_single_instance():
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    import os
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            import signal
            os.kill(old_pid, 0)
            print(f"{SERVICE_NAME} already running as PID {old_pid}")
            return False
        except OSError:
            pass
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    import os
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    try:
        os.unlink(pid_file)
    except OSError:
        pass


def signal_handler(signum, frame):
    import os
    log(f"Received signal {signum}, shutting down")
    remove_pid_file()
    os._exit(0)


def run():
    """Forever loop - run flows on a schedule."""
    import signal
    import os

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    if not check_single_instance():
        return

    log(f"{SERVICE_NAME} starting")
    CYCLE_SECS = 300  # Run flows every 5 minutes

    while True:
        try:
            results = run_all_flows()
            log(f"Cycle complete: total_passed={results['total_passed']}")
            send_heartbeat()
        except Exception as ex:
            log(f"Cycle error: {ex}")
        time.sleep(CYCLE_SECS)


def main():
    """One-shot run of all flows (for direct invocation)."""
    log(f"{SERVICE_NAME} one-shot run")
    results = run_all_flows()
    send_heartbeat()
    import sys
    sys.exit(0 if results["total_passed"] else 1)


if __name__ == "__main__":
    main()