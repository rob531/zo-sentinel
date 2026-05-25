import sys
import time
import json
from datetime import datetime, timedelta

sys.path.insert(0, '/home/workspace/zo_sentinel')

SERVICE_NAME = "verify_signal_analyser_scoring"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"
HEARTBEAT_INTERVAL = 60
POLL_SECS = 300

SIGNAL_TYPES = [
    "permission_scope",
    "temporal_stability", 
    "tool_description_safety",
    "supply_chain",
    "domain_trust",
    "community_signal",
    "context_efficiency",
    "evidence_density",
    "injection_resilience",
]

ENRICHMENT_FILES = [
    "permission_scope_enrichment_v3.py",
    "temporal_stability_enrichment_v4.py",
    "tool_description_safety_enrichment_v4.py",
]


def log(msg):
    ts = datetime.utcnow().isoformat()
    print(f"[{ts}] {msg}", flush=True)


def ws_query(sql):
    try:
        import requests
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Query failed: {e}")
        return {"rows": [], "count": 0}


def ws_write(table, rows):
    try:
        import requests
        resp = requests.post(WRITE_URL, json={"table": table, "rows": rows}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Write failed: {e}")
        return {"ok": False}


def send_heartbeat():
    now = datetime.utcnow().isoformat()
    ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": now})


def check_single_instance():
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    try:
        with open(pid_file, 'r') as f:
            existing = int(f.read().strip())
        import os
        if existing != os.getpid():
            import os
            try:
                os.kill(existing, 0)
                log(f"Already running as PID {existing}, exiting")
                return False
            except OSError:
                log(f"Stale PID file from {existing}")
    except FileNotFoundError:
        pass
    
    with open(pid_file, 'w') as f:
        import os
        f.write(str(os.getpid()))
    return True


def get_enrichment_signal_types():
    log("Checking available enrichment signal types in mcp_signal_enrichments table...")
    result = ws_query("""
        SELECT DISTINCT signal_type 
        FROM mcp_signal_enrichments 
        ORDER BY signal_type
    """)
    rows = result.get("rows", [])
    signal_types = [r.get("signal_type") for r in rows if r.get("signal_type")]
    log(f"Found {len(signal_types)} signal types in enrichments: {signal_types}")
    return signal_types


def get_recent_enrichments(hours=24):
    log(f"Fetching enrichments from last {hours} hours...")
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    result = ws_query(f"""
        SELECT server_id, signal_type, score, evidence, scored_at
        FROM mcp_signal_enrichments
        WHERE scored_at >= '{cutoff}'
        ORDER BY scored_at DESC
    """)
    return result.get("rows", [])


def get_all_enrichments():
    log("Fetching all enrichments (no time filter)...")
    result = ws_query("""
        SELECT server_id, signal_type, score, evidence, scored_at
        FROM mcp_signal_enrichments
        ORDER BY scored_at DESC
    """)
    return result.get("rows", [])


def get_scores_for_servers(server_ids, signal_types):
    if not server_ids:
        return {}
    placeholders = ",".join([f"'{s}'" for s in server_ids])
    type_placeholders = ",".join([f"'{s}'" for s in signal_types])
    result = ws_query(f"""
        SELECT server_id, signal_type, score, evidence, scored_at
        FROM mcp_signal_scores
        WHERE server_id IN ({placeholders})
        AND signal_type IN ({type_placeholders})
    """)
    scores = {}
    for row in result.get("rows", []):
        key = (row.get("server_id"), row.get("signal_type"))
        scores[key] = row
    return scores


def check_enrichment_to_score_wiring(enrichments, signal_types):
    log("Checking enrichment -> score wiring...")
    gaps = []
    servers_seen = set()
    for e in enrichments:
        servers_seen.add(e.get("server_id"))
    
    log(f"Found {len(servers_seen)} unique servers with enrichments")
    
    scores = get_scores_for_servers(list(servers_seen), signal_types)
    
    for e in enrichments:
        server_id = e.get("server_id")
        signal_type = e.get("signal_type")
        key = (server_id, signal_type)
        if key not in scores:
            gaps.append({
                "server_id": server_id,
                "signal_type": signal_type,
                "enrichment_score": e.get("score"),
                "enrichment_scored_at": e.get("scored_at"),
            })
    
    return gaps


def check_specific_enrichments_wired():
    log("Checking specific v3/v4 enrichment modules are being consumed...")
    results = {}
    
    for signal_type in SIGNAL_TYPES:
        enrichment_count = 0
        score_count = 0
        
        e_result = ws_query(f"""
            SELECT COUNT(*) as cnt FROM mcp_signal_enrichments 
            WHERE signal_type = '{signal_type}'
        """)
        if e_result.get("rows"):
            enrichment_count = e_result["rows"][0].get("cnt", 0)
        
        s_result = ws_query(f"""
            SELECT COUNT(*) as cnt FROM mcp_signal_scores 
            WHERE signal_type = '{signal_type}'
        """)
        if s_result.get("rows"):
            score_count = s_result["rows"][0].get("cnt", 0)
        
        results[signal_type] = {
            "enrichment_count": enrichment_count,
            "score_count": score_count,
            "wired": enrichment_count > 0 and score_count > 0,
            "gap": enrichment_count > 0 and score_count == 0,
        }
    
    return results


def get_servers_with_missing_scores(enrichments, signal_types):
    log("Identifying servers with enrichment rows but missing score rows...")
    
    server_signal_map = {}
    for e in enrichments:
        sid = e.get("server_id")
        stype = e.get("signal_type")
        if sid not in server_signal_map:
            server_signal_map[sid] = set()
        server_signal_map[sid].add(stype)
    
    gap_servers = {}
    
    for server_id, signal_types_set in server_signal_map.items():
        for signal_type in signal_types_set:
            check_result = ws_query(f"""
                SELECT COUNT(*) as cnt FROM mcp_signal_scores 
                WHERE server_id = '{server_id}' 
                AND signal_type = '{signal_type}'
            """)
            count = 0
            if check_result.get("rows"):
                count = check_result["rows"][0].get("cnt", 0)
            
            if count == 0:
                if server_id not in gap_servers:
                    gap_servers[server_id] = []
                gap_servers[server_id].append(signal_type)
    
    return gap_servers


def get_registry_info(server_ids):
    if not server_ids:
        return {}
    placeholders = ",".join([f"'{s}'" for s in server_ids])
    result = ws_query(f"""
        SELECT server_id, name, url, trust_score, verdict
        FROM mcp_server_registry
        WHERE server_id IN ({placeholders})
    """)
    info = {}
    for row in result.get("rows", []):
        info[row.get("server_id")] = row
    return info


def run():
    log("=" * 60)
    log(f"Starting {SERVICE_NAME}")
    log("=" * 60)
    
    if not check_single_instance():
        return
    
    try:
        send_heartbeat()
        
        log("\n--- Step 1: Check available signal types ---")
        available_signals = get_enrichment_signal_types()
        
        log("\n--- Step 2: Get recent enrichments (24h) ---")
        recent_enrichments = get_recent_enrichments(24)
        log(f"Found {len(recent_enrichments)} recent enrichment rows")
        
        log("\n--- Step 3: Get all-time enrichments ---")
        all_enrichments = get_all_enrichments()
        log(f"Found {len(all_enrichments)} total enrichment rows")
        
        log("\n--- Step 4: Check specific enrichment modules wiring ---")
        wiring_results = check_specific_enrichments_wired()
        
        print("\n" + "=" * 80)
        print("ENRICHMENT -> SCORE WIRING REPORT")
        print("=" * 80)
        
        unwired = []
        for signal_type, info in wiring_results.items():
            status = "WIRED" if info["wired"] else "UNWIRED"
            gap_marker = " [GAP]" if info["gap"] else ""
            print(f"  {signal_type:30} enrichments={info['enrichment_count']:6} scores={info['score_count']:6} [{status}]{gap_marker}")
            if info["gap"]:
                unwired.append(signal_type)
        
        log("\n--- Step 5: Identify servers with wiring gaps ---")
        gap_servers = get_servers_with_missing_scores(all_enrichments, available_signals)
        
        print("\n" + "-" * 80)
        print("SERVERS WITH ENRICHMENT ROWS BUT MISSING SCORE ROWS (WIRING GAPS)")
        print("-" * 80)
        
        if gap_servers:
            server_ids = list(gap_servers.keys())
            registry_info = get_registry_info(server_ids)
            
            for server_id, missing_signals in gap_servers.items():
                info = registry_info.get(server_id, {})
                name = info.get("name", "unknown")
                url = info.get("url", "unknown")
                verdict = info.get("verdict", "unknown")
                print(f"\n  Server: {server_id}")
                print(f"    Name: {name}")
                print(f"    URL: {url}")
                print(f"    Verdict: {verdict}")
                print(f"    Missing signals: {missing_signals}")
        else:
            print("\n  No wiring gaps detected - all enrichments have corresponding scores")
        
        log("\n--- Step 6: Summary ---")
        total_gap_servers = len(gap_servers)
        total_unwired_signals = len(unwired)
        
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"  Total servers with enrichments: {len(set(e.get('server_id') for e in all_enrichments))}")
        print(f"  Servers with wiring gaps: {total_gap_servers}")
        print(f"  Unwired signal types: {total_unwired_signals}")
        if unwired:
            print(f"  Details: {unwired}")
        
        send_heartbeat()
        
    except Exception as e:
        log(f"Error during verification: {e}")
        import traceback
        traceback.print_exc()
    
    log(f"{SERVICE_NAME} completed")


if __name__ == "__main__":
    run()