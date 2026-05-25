#!/usr/bin/env python3
import requests
import json
import time
from datetime import datetime

SERVICE_NAME = "diagnose_tool_description_safety"
WRITE_SERVICE = "http://127.0.0.1:8772/write"
QUERY_SERVICE = "http://127.0.0.1:8772/query"
POLL_SECS = 300

def query(sql):
    resp = requests.post(QUERY_SERVICE, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def write_diagnostic(table, rows):
    resp = requests.post(WRITE_SERVICE, json={"table": table, "rows": rows, "wait": True}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def check_single_instance():
    import os
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        if old_pid != os.getpid() and os.path.exists(f"/proc/{old_pid}"):
            print(f"[ABORT] Another instance already running (PID {old_pid})")
            exit(0)
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

def send_heartbeat():
    timestamp = datetime.utcnow().isoformat()
    write_diagnostic("service_health", {"service": SERVICE_NAME, "last_heartbeat": timestamp})

def run():
    check_single_instance()
    print(f"[{SERVICE_NAME}] Starting diagnosis of tool_description_safety enrichment effectiveness")
    
    diagnostics = {
        "timestamp": datetime.utcnow().isoformat(),
        "signal_type": "tool_description_safety",
        "distinct_scores": [],
        "score_distribution": {},
        "sample_evidence": [],
        "metadata_fields_found": set(),
        "issues_detected": [],
        "recommendation": ""
    }
    
    try:
        count_sql = """
        SELECT COUNT(*) as total_rows
        FROM mcp_signal_enrichments
        WHERE signal_type = 'tool_description_safety'
        """
        count_result = query(count_sql)
        total_rows = count_result.get("rows", [{}])[0].get("total_rows", 0)
        diagnostics["total_enrichment_rows"] = total_rows
        print(f"Total tool_description_safety enrichment rows: {total_rows}")
        
        distinct_sql = """
        SELECT DISTINCT score as distinct_score
        FROM mcp_signal_enrichments
        WHERE signal_type = 'tool_description_safety'
        AND score IS NOT NULL
        ORDER BY distinct_score
        """
        distinct_result = query(distinct_sql)
        distinct_scores = [r.get("distinct_score") for r in distinct_result.get("rows", [])]
        diagnostics["distinct_scores"] = distinct_scores
        diagnostics["distinct_score_count"] = len(distinct_scores)
        print(f"Distinct scores found: {distinct_scores}")
        print(f"Distinct score count: {len(distinct_scores)}")
        
        if len(distinct_scores) < 5:
            diagnostics["issues_detected"].append(f"LOW_DIVERSITY: Only {len(distinct_scores)} distinct score values found")
        
        dist_sql = """
        SELECT score, COUNT(*) as count
        FROM mcp_signal_enrichments
        WHERE signal_type = 'tool_description_safety'
        AND score IS NOT NULL
        GROUP BY score
        ORDER BY count DESC
        """
        dist_result = query(distist_sql)
        for row in dist_result.get("rows", []):
            score = row.get("score")
            cnt = row.get("count", 0)
            diagnostics["score_distribution"][str(score)] = cnt
        print(f"Score distribution: {diagnostics['score_distribution']}")
        
        sample_sql = """
        SELECT server_id, score, evidence, metadata, enriched_at
        FROM mcp_signal_enrichments
        WHERE signal_type = 'tool_description_safety'
        AND evidence IS NOT NULL
        LIMIT 10
        """
        sample_result = query(sample_sql)
        for row in sample_result.get("rows", []):
            evidence = row.get("evidence", "")
            metadata = row.get("metadata", "")
            sample_entry = {
                "server_id": row.get("server_id"),
                "score": row.get("score"),
                "evidence_length": len(str(evidence)) if evidence else 0,
                "metadata_length": len(str(metadata)) if metadata else 0
            }
            
            if metadata:
                try:
                    metadata_obj = json.loads(metadata) if isinstance(metadata, str) else metadata
                    diagnostics["metadata_fields_found"].update(metadata_obj.keys())
                    sample_entry["metadata_fields"] = list(metadata_obj.keys())
                except:
                    pass
            
            diagnostics["sample_evidence"].append(sample_entry)
        
        if diagnostics["metadata_fields_found"]:
            diagnostics["metadata_fields_found"] = list(diagnostics["metadata_fields_found"])
        else:
            diagnostics["metadata_fields_found"] = []
        
        print(f"Sample evidence analyzed: {len(diagnostics['sample_evidence'])}")
        print(f"Metadata fields found across samples: {diagnostics['metadata_fields_found']}")
        
        empty_evidence_count_sql = """
        SELECT COUNT(*) as empty_count
        FROM mcp_signal_enrichments
        WHERE signal_type = 'tool_description_safety'
        AND (evidence IS NULL OR evidence = '' OR evidence = '{}')
        """
        empty_result = query(empty_evidence_count_sql)
        empty_count = empty_result.get("rows", [{}])[0].get("empty_count", 0)
        diagnostics["empty_evidence_count"] = empty_count
        
        if total_rows > 0:
            empty_ratio = empty_count / total_rows
            diagnostics["empty_evidence_ratio"] = round(empty_ratio, 4)
            if empty_ratio > 0.5:
                diagnostics["issues_detected"].append(f"HIGH_EMPTY_EVIDENCE: {empty_ratio*100:.1f}% of rows have empty evidence")
        
        null_score_count_sql = """
        SELECT COUNT(*) as null_score_count
        FROM mcp_signal_enrichments
        WHERE signal_type = 'tool_description_safety'
        AND score IS NULL
        """
        null_result = query(null_score_count_sql)
        null_score_count = null_result.get("rows", [{}])[0].get("null_score_count", 0)
        diagnostics["null_score_count"] = null_score_count
        
        if total_rows > 0:
            null_ratio = null_score_count / total_rows
            diagnostics["null_score_ratio"] = round(null_ratio, 4)
            if null_ratio > 0.3:
                diagnostics["issues_detected"].append(f"HIGH_NULL_SCORE: {null_ratio*100:.1f}% of rows have NULL scores")
        
        check_enrichment_date_sql = """
        SELECT MIN(enriched_at) as earliest, MAX(enriched_at) as latest, COUNT(DISTINCT DATE(enriched_at)) as distinct_days
        FROM mcp_signal_enrichments
        WHERE signal_type = 'tool_description_safety'
        AND enriched_at IS NOT NULL
        """
        date_result = query(check_enrichment_date_sql)
        date_row = date_result.get("rows", [{}])[0]
        diagnostics["enrichment_date_range"] = {
            "earliest": date_row.get("earliest"),
            "latest": date_row.get("latest"),
            "distinct_days": date_row.get("distinct_days", 0)
        }
        
        if date_row.get("distinct_days", 0) <= 1 and total_rows > 100:
            diagnostics["issues_detected"].append("SINGLE_BATCH: All enrichments may be from single run")
        
        if diagnostics["issues_detected"]:
            diagnostics["recommendation"] = "ISSUES_FOUND: Review tool_description_safety_enrichment.py for scoring logic"
        elif len(distinct_scores) >= 5:
            diagnostics["recommendation"] = "HEALTHY: Signal diversity appears adequate"
        else:
            diagnostics["recommendation"] = "WEAK_DIVERSITY: Enrichment exists but scoring granularity insufficient"
        
        diagnostic_text = json.dumps(diagnostics, indent=2)
        print("\n=== DIAGNOSTIC RESULTS ===")
        print(diagnostic_text)
        
        health_payload = {
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.utcnow().isoformat(),
            "diagnostic_output": diagnostic_text[:8000]
        }
        
        write_result = write_diagnostic("service_health", health_payload)
        print(f"Diagnostic written to service_health: {write_result}")
        
    except Exception as e:
        error_msg = f"ERROR: {str(e)}"
        print(error_msg)
        diagnostics["fatal_error"] = error_msg
        try:
            write_diagnostic("service_health", {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.utcnow().isoformat(),
                "diagnostic_output": json.dumps(diagnostics)[:8000]
            })
        except:
            pass
    
    print(f"[{SERVICE_NAME}] Diagnosis complete")
    return diagnostics

if __name__ == "__main__":
    run()
    while True:
        time.sleep(POLL_SECS)
        send_heartbeat()
        run()