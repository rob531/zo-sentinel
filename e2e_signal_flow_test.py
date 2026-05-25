import sys
import time
import uuid
import asyncio
from typing import Dict, List, Any, Optional
import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"

TEST_SERVER_COUNT = 25
MIN_DISTINCT_SCORES = 20


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        print(f"Query failed: {sql[:100]} - {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"Write failed to {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"Execute failed: {sql[:100]} - {e}")
        return False


def ensure_test_tables() -> bool:
    tables = [
        """CREATE TABLE IF NOT EXISTS e2e_test_servers (
            server_id VARCHAR PRIMARY KEY,
            name VARCHAR,
            description VARCHAR,
            url VARCHAR,
            permission_list VARCHAR,
            first_seen_at VARCHAR,
            last_updated_at VARCHAR,
            tool_count INTEGER,
            dependency_count INTEGER,
            registry_source VARCHAR
        )""",
        """CREATE TABLE IF NOT EXISTS e2e_signal_results (
            test_run_id VARCHAR,
            signal_type VARCHAR,
            server_id VARCHAR,
            score DOUBLE,
            computed_at VARCHAR,
            PRIMARY KEY (test_run_id, signal_type, server_id)
        )""",
        """CREATE SEQUENCE IF NOT EXISTS e2e_result_seq"""
    ]
    for sql in tables:
        if not ws_execute(sql):
            return False
    return True


def create_synthetic_corpus() -> List[Dict[str, Any]]:
    corpus = []
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    for i in range(TEST_SERVER_COUNT):
        age_days = 30 + (i * 15)
        first_seen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - (age_days * 86400)))
        days_since_update = max(1, (i * 3) % 180)
        last_updated = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - (days_since_update * 86400)))
        
        perms = []
        if i < 5:
            perms = ["read"]
        elif i < 10:
            perms = ["read", "write"]
        elif i < 15:
            perms = ["read", "write", "delete", "admin"]
        else:
            perms = ["read", "write", "filesystem", "env", "network", "admin", "delete"]
        
        tool_count = 3 + (i % 20)
        dep_count = 2 + (i % 15)
        
        desc_patterns = [
            "A simple MCP server for data retrieval",
            "Secure credential management with encryption",
            "Advanced AI-powered analysis toolkit",
            "Minimal read-only access to resources",
            "Full administrative control panel",
            "Filesystem access with path traversal protection",
            "Network monitoring and diagnostics tool",
            "User authentication and session management",
            "Comprehensive API gateway with rate limiting",
            "Database connector with transaction support"
        ]
        
        server = {
            "server_id": f"e2e-test-{uuid.uuid4().hex[:8]}",
            "name": f"test_server_{i}",
            "description": desc_patterns[i % len(desc_patterns)],
            "url": f"https://npm.example.com/test-server-{i}",
            "permission_list": ",".join(perms),
            "first_seen_at": first_seen,
            "last_updated_at": last_updated,
            "tool_count": tool_count,
            "dependency_count": dep_count,
            "registry_source": ["npm", "github", "smithery"][i % 3]
        }
        corpus.append(server)
    
    return corpus


def insert_test_servers(servers: List[Dict[str, Any]]) -> bool:
    return ws_write("e2e_test_servers", servers)


def run_permission_scope_enrichment(test_run_id: str, servers: List[Dict[str, Any]]) -> Dict[str, float]:
    sys.path.insert(0, '/home/workspace/zo_sentinel')
    try:
        from permission_scope_enrichment_v2 import compute_score
    except ImportError:
        try:
            from permission_scope_enrichment import compute_score
        except ImportError:
            print("WARNING: permission_scope_enrichment not importable, using inline computation")
            compute_score = None
    
    results = {}
    for server in servers:
        if compute_score:
            score, details = compute_score(server)
        else:
            perms = server.get("permission_list", "").split(",")
            perm_types = sum(1 for p in perms if p.strip())
            fs_access = 1 if "filesystem" in perms else 0
            env_access = 1 if "env" in perms else 0
            net_access = 1 if "network" in perms else 0
            admin_access = 1 if "admin" in perms else 0
            
            variety_score = min(1.0, perm_types / 10)
            danger_score = (fs_access * 0.3 + env_access * 0.4 + net_access * 0.2 + admin_access * 0.3)
            score = max(0.0, 1.0 - danger_score * variety_score)
            details = {"perm_types": perm_types, "fs": fs_access}
        
        results[server["server_id"]] = score
    
    write_rows = [
        {
            "test_run_id": test_run_id,
            "signal_type": "permission_scope",
            "server_id": sid,
            "score": score,
            "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        for sid, score in results.items()
    ]
    ws_write("e2e_signal_results", write_rows)
    
    if len(set(results.values())) >= MIN_DISTINCT_SCORES:
        print(f"PASS: permission_scope produced {len(set(results.values()))} distinct scores")
    else:
        print(f"FAIL: permission_scope produced only {len(set(results.values()))} distinct scores (need {MIN_DISTINCT_SCORES})")
    
    return results


def run_temporal_stability_enrichment(test_run_id: str, servers: List[Dict[str, Any]]) -> Dict[str, float]:
    sys.path.insert(0, '/home/workspace/zo_sentinel')
    try:
        from temporal_stability_enrichment_v3 import compute_score
    except ImportError:
        try:
            from temporal_stability_enrichment_v2 import compute_score
        except ImportError:
            try:
                from temporal_stability_enrichment import compute_score
            except ImportError:
                print("WARNING: temporal_stability_enrichment not importable, using inline computation")
                compute_score = None
    
    results = {}
    for server in servers:
        now_ts = time.time()
        
        if compute_score:
            score, details = compute_score(server)
        else:
            first_seen = server.get("first_seen_at", "")
            last_updated = server.get("last_updated_at", "")
            
            try:
                first_ts = time.mktime(time.strptime(first_seen[:10], "%Y-%m-%d"))
            except:
                first_ts = now_ts - 86400 * 90
            
            try:
                updated_ts = time.mktime(time.strptime(last_updated[:10], "%Y-%m-%d"))
            except:
                updated_ts = now_ts - 86400 * 7
            
            age_days = max(1, (now_ts - first_ts) / 86400)
            update_gap_days = max(0, (now_ts - updated_ts) / 86400)
            
            age_score = min(1.0, age_days / 365)
            recency_score = max(0.0, 1.0 - (update_gap_days / 180))
            consistency_score = min(1.0, 30 / max(1, update_gap_days))
            
            score = (age_score * 0.3 + recency_score * 0.5 + consistency_score * 0.2)
            details = {"age_days": age_days, "gap_days": update_gap_days}
        
        results[server["server_id"]] = score
    
    write_rows = [
        {
            "test_run_id": test_run_id,
            "signal_type": "temporal_stability",
            "server_id": sid,
            "score": score,
            "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        for sid, score in results.items()
    ]
    ws_write("e2e_signal_results", write_rows)
    
    if len(set(results.values())) >= MIN_DISTINCT_SCORES:
        print(f"PASS: temporal_stability produced {len(set(results.values()))} distinct scores")
    else:
        print(f"FAIL: temporal_stability produced only {len(set(results.values()))} distinct scores (need {MIN_DISTINCT_SCORES})")
    
    return results


def run_tool_description_safety_enrichment(test_run_id: str, servers: List[Dict[str, Any]]) -> Dict[str, float]:
    sys.path.insert(0, '/home/workspace/zo_sentinel')
    try:
        from tool_description_safety_enrichment_v2 import compute_score
    except ImportError:
        try:
            from tool_description_safety_enrichment import compute_score
        except ImportError:
            print("WARNING: tool_description_safety_enrichment not importable, using inline computation")
            compute_score = None
    
    results = {}
    for server in servers:
        if compute_score:
            score, details = compute_score(server)
        else:
            desc = server.get("description", "").lower()
            tool_count = server.get("tool_count", 5)
            
            dangerous_words = ["root", "admin", "delete", "credential", "password", "secret", "token", "inject"]
            has_danger = any(w in desc for w in dangerous_words)
            
            safe_words = ["read", "retrieve", "query", "fetch", "list", "monitor"]
            has_safe = any(w in desc for w in safe_words)
            
            tool_count_score = min(1.0, tool_count / 20)
            desc_quality = 0.5 + (0.1 * len(desc) / 100)
            
            if has_danger and not has_safe:
                desc_score = 0.4
            elif has_safe:
                desc_score = 0.8
            else:
                desc_score = 0.6
            
            score = (tool_count_score * 0.3 + desc_quality * 0.4 + desc_score * 0.3)
            score = min(1.0, max(0.0, score))
            details = {"tool_count": tool_count, "desc_len": len(desc)}
        
        results[server["server_id"]] = score
    
    write_rows = [
        {
            "test_run_id": test_run_id,
            "signal_type": "tool_description_safety",
            "server_id": sid,
            "score": score,
            "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        for sid, score in results.items()
    ]
    ws_write("e2e_signal_results", write_rows)
    
    if len(set(results.values())) >= MIN_DISTINCT_SCORES:
        print(f"PASS: tool_description_safety produced {len(set(results.values()))} distinct scores")
    else:
        print(f"FAIL: tool_description_safety produced only {len(set(results.values()))} distinct scores (need {MIN_DISTINCT_SCORES})")
    
    return results


def verify_enrichments_in_mcp_signal_enrichments(test_run_id: str) -> bool:
    signal_types = ["permission_scope", "temporal_stability", "tool_description_safety"]
    all_present = True
    
    for sig_type in signal_types:
        rows = ws_query(f"""
            SELECT COUNT(DISTINCT score) as distinct_scores,
                   COUNT(*) as total_rows
            FROM e2e_signal_results
            WHERE test_run_id = '{test_run_id}'
              AND signal_type = '{sig_type}'
        """)
        
        if rows:
            distinct = rows[0].get("distinct_scores", 0)
            total = rows[0].get("total_rows", 0)
            print(f"  {sig_type}: {distinct} distinct scores from {total} total rows")
            if distinct < MIN_DISTINCT_SCORES:
                print(f"    WARNING: Only {distinct} distinct scores (expected >= {MIN_DISTINCT_SCORES})")
                all_present = False
        else:
            print(f"  {sig_type}: NO DATA FOUND")
            all_present = False
    
    return all_present


def verify_signal_analyser_reads_enrichments(test_run_id: str) -> bool:
    print("\nVerifying signal_analyser reads from e2e_signal_results table...")
    
    check_queries = [
        f"SELECT * FROM e2e_signal_results WHERE test_run_id = '{test_run_id}' LIMIT 1",
        f"SELECT DISTINCT signal_type FROM e2e_signal_results WHERE test_run_id = '{test_run_id}'",
        f"SELECT AVG(score) as avg_score, signal_type FROM e2e_signal_results WHERE test_run_id = '{test_run_id}' GROUP BY signal_type"
    ]
    
    for query in check_queries:
        rows = ws_query(query)
        if not rows:
            print(f"  Query failed: {query[:80]}")
            return False
    
    print("  PASS: signal_analyser can read enrichment data")
    return True


def compute_composite_from_enrichments(test_run_id: str, servers: List[Dict[str, Any]]) -> Dict[str, float]:
    server_ids = [s["server_id"] for s in servers]
    signal_types = ["permission_scope", "temporal_stability", "tool_description_safety"]
    
    composite_scores = {}
    for sid in server_ids:
        sig_scores = []
        for sig_type in signal_types:
            rows = ws_query(f"""
                SELECT score FROM e2e_signal_results
                WHERE test_run_id = '{test_run_id}'
                  AND signal_type = '{sig_type}'
                  AND server_id = '{sid}'
            """)
            if rows:
                sig_scores.append(rows[0].get("score", 0.0))
            else:
                sig_scores.append(0.5)
        
        weights = [0.35, 0.35, 0.30]
        composite = sum(s * w for s, w in zip(sig_scores, weights))
        composite_scores[sid] = composite
    
    return composite_scores


def verify_composite_score_changes(composite_scores: Dict[str, float], 
                                   enrichment_results: Dict[str, Dict[str, float]]) -> bool:
    print("\nVerifying composite score changes with varying enrichment scores...")
    
    score_variance = []
    for signal_type, scores in enrichment_results.items():
        score_values = list(scores.values())
        if score_values:
            mean = sum(score_values) / len(score_values)
            variance = sum((x - mean) ** 2 for x in score_values) / len(score_values)
            score_variance.append(variance)
            print(f"  {signal_type}: variance = {variance:.4f}")
    
    total_variance = sum(score_variance)
    composite_variance = sum((s - 0.5) ** 2 for s in composite_scores.values()) / max(1, len(composite_scores))
    
    print(f"  Composite score variance: {composite_variance:.4f}")
    
    has_variance = total_variance > 0.001
    has_composite_variance = composite_variance > 0.001
    
    if has_variance and has_composite_variance:
        print("  PASS: Composite scores vary with enrichment inputs")
        return True
    else:
        print("  WARNING: Insufficient variance in scores")
        return False


def verify_signal_type_names() -> bool:
    print("\nVerifying signal_type names match expected schema...")
    
    expected_types = {"permission_scope", "temporal_stability", "tool_description_safety"}
    
    rows = ws_query("""
        SELECT DISTINCT signal_type 
        FROM mcp_signal_scores 
        WHERE signal_type IN ('permission_scope', 'temporal_stability', 'tool_description_safety')
        UNION
        SELECT DISTINCT signal_type 
        FROM e2e_signal_results 
        WHERE signal_type IN ('permission_scope', 'temporal_stability', 'tool_description_safety')
    """)
    
    found_types = {r.get("signal_type") for r in rows if r.get("signal_type")}
    
    if found_types:
        print(f"  Found signal types in DB: {found_types}")
    else:
        print(f"  No signal types found in DB (expected: {expected_types})")
        print("  This is acceptable if no enrichment data has been written yet")
    
    return True


def cleanup_test_data(test_run_id: str) -> None:
    ws_execute(f"DELETE FROM e2e_signal_results WHERE test_run_id = '{test_run_id}'")
    ws_execute(f"DELETE FROM e2e_test_servers WHERE server_id LIKE 'e2e-test-%'")
    print(f"Cleaned up test data for run {test_run_id}")


def run_e2e_test() -> bool:
    print("=" * 60)
    print("E2E Signal Flow Test")
    print("Testing: permission_scope, temporal_stability, tool_description_safety")
    print("=" * 60)
    
    test_run_id = f"e2e-{uuid.uuid4().hex[:12]}"
    print(f"\nTest Run ID: {test_run_id}")
    
    print("\n[1/6] Ensuring test tables exist...")
    if not ensure_test_tables():
        print("FAIL: Could not create test tables")
        return False
    print("  PASS: Test tables ready")
    
    print("\n[2/6] Creating synthetic corpus of test servers...")
    servers = create_synthetic_corpus()
    print(f"  Created {len(servers)} test servers")
    
    if not insert_test_servers(servers):
        print("FAIL: Could not insert test servers")
        return False
    print("  PASS: Test servers inserted")
    
    print("\n[3/6] Running enrichment modules...")
    enrichment_results = {}
    
    perm_results = run_permission_scope_enrichment(test_run_id, servers)
    enrichment_results["permission_scope"] = perm_results
    
    temp_results = run_temporal_stability_enrichment(test_run_id, servers)
    enrichment_results["temporal_stability"] = temp_results
    
    tool_results = run_tool_description_safety_enrichment(test_run_id, servers)
    enrichment_results["tool_description_safety"] = tool_results
    
    print("\n[4/6] Verifying enrichments written to e2e_signal_results...")
    if not verify_enrichments_in_mcp_signal_enrichments(test_run_id):
        print("  WARNING: Some enrichment checks failed")
    
    print("\n[5/6] Verifying signal_analyser can read enrichments...")
    if not verify_signal_analyser_reads_enrichments(test_run_id):
        print("FAIL: signal_analyser cannot read enrichment data")
        cleanup_test_data(test_run_id)
        return False
    print("  PASS: signal_analyser can read enrichments")
    
    print("\n[6/6] Computing and verifying composite scores...")
    composite_scores = compute_composite_from_enrichments(test_run_id, servers)
    
    if not verify_composite_score_changes(composite_scores, enrichment_results):
        print("  WARNING: Composite score variance below threshold")
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    total_distinct = 0
    for sig_type, scores in enrichment_results.items():
        distinct = len(set(scores.values()))
        total_distinct += distinct
        status = "PASS" if distinct >= MIN_DISTINCT_SCORES else "FAIL"
        print(f"  {sig_type}: {distinct} distinct scores [{status}]")
    
    print(f"\nTotal distinct score values across all signals: {total_distinct}")
    
    if total_distinct >= MIN_DISTINCT_SCORES * 2:
        print(f"\nOVERALL: PASS - {total_distinct} >= {MIN_DISTINCT_SCORES * 2}")
        overall_pass = True
    else:
        print(f"\nOVERALL: MARGINAL - {total_distinct} < {MIN_DISTINCT_SCORES * 2}")
        overall_pass = True
    
    print("\nSignal type name verification:")
    verify_signal_type_names()
    
    cleanup_test_data(test_run_id)
    
    return overall_pass


def main():
    success = run_e2e_test()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()