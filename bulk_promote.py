#!/usr/bin/env python3
import requests
import sys
import time

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
BATCH_SIZE = 500


def ws_query(sql):
    resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=60)
    resp.raise_for_status()
    return resp.json()


def ws_write(table, rows):
    resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=60)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql):
    resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=60)
    resp.raise_for_status()
    return resp.json()


def get_registry_count():
    result = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
    return result.get("rows", [{}])[0].get("cnt", 0)


def get_unpromoted_candidates():
    result = ws_query(
        "SELECT id, candidate_name, candidate_url, candidate_description "
        "FROM mcp_discovery_candidates "
        "WHERE promoted=false AND discovered_status='active' AND discovered_in_directory='mcp_registry'"
    )
    return result.get("rows", [])


def promote_batch(candidates):
    if not candidates:
        return 0
    
    batch_ids = [c["id"] for c in candidates]
    
    insert_rows = []
    for c in candidates:
        insert_rows.append({
            "server_id": None,
            "name": c.get("candidate_name", "unknown"),
            "url": c.get("candidate_url", ""),
            "description": c.get("candidate_description", ""),
            "verdict": "unknown",
            "trust_score": 0.5,
            "created_at": None
        })
    
    ws_write("mcp_server_registry", insert_rows)
    
    placeholders = ",".join([f"'{cid}'" for cid in batch_ids])
    ws_execute(
        f"UPDATE mcp_discovery_candidates SET promoted=true, reviewed_at=now() WHERE id IN ({placeholders})"
    )
    
    return len(candidates)


def run():
    print(f"[bulk_promote] Starting bulk promotion of mcp_registry candidates...")
    
    before_count = get_registry_count()
    print(f"[bulk_promote] mcp_server_registry count BEFORE: {before_count}")
    
    all_candidates = get_unpromoted_candidates()
    total_candidates = len(all_candidates)
    print(f"[bulk_promote] Found {total_candidates} unpromoted candidates")
    
    if total_candidates == 0:
        print("[bulk_promote] No candidates to promote. Exiting.")
        after_count = get_registry_count()
        print(f"[bulk_promote] mcp_server_registry count AFTER: {after_count}")
        return
    
    total_promoted = 0
    for i in range(0, total_candidates, BATCH_SIZE):
        batch = all_candidates[i:i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1
        print(f"[bulk_promote] Processing batch {batch_num}: {len(batch)} candidates...")
        
        try:
            promoted = promote_batch(batch)
            total_promoted += promoted
            print(f"[bulk_promote] Batch {batch_num} complete: {promoted} promoted")
        except Exception as e:
            print(f"[bulk_promote] ERROR in batch {batch_num}: {e}")
            continue
        
        time.sleep(0.5)
    
    after_count = get_registry_count()
    print(f"[bulk_promote] mcp_server_registry count AFTER: {after_count}")
    print(f"[bulk_promote] Total promoted: {total_promoted}")
    print(f"[bulk_promote] Registry grew by: {after_count - before_count}")
    print(f"[bulk_promote] Bulk promotion complete.")


if __name__ == "__main__":
    run()