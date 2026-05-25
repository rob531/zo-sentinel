import sys
sys.path.insert(0, '/home/workspace')

import requests
import json
import hashlib
from datetime import datetime, timezone

WRITE_SERVICE_URL = 'http://127.0.0.1:8772'

def ws_query(sql, params=None):
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_execute(sql, params=None):
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(f"{WRITE_SERVICE_URL}/execute", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def build_validation_report():
    report = {
        "check_name": "tool_description_enrichment_evidence",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "gate": "enrichment_evidence",
        "threshold": 20,
        "references": ["cohort_9_n4"]
    }
    
    # SQL validation: count distinct score values in mcp_fingerprints
    # that originate from tool_description_safety_enrichment.py processing
    validation_sql = """
    WITH enrichment_source AS (
        SELECT DISTINCT server_id
        FROM mcp_signal_scores
        WHERE signal_name = 'tool_description_safety'
          AND evidence IS NOT NULL
          AND evidence NOT LIKE '%error%'
    ),
    fingerprint_scores AS (
        SELECT 
            fp.fingerprint_id,
            fp.server_id,
            fp.score AS safety_score,
            fp.enrichment_source
        FROM mcp_fingerprints fp
        INNER JOIN enrichment_source es ON fp.server_id = es.server_id
        WHERE fp.score IS NOT NULL
    ),
    distinct_scores AS (
        SELECT COUNT(DISTINCT safety_score) AS score_count
        FROM fingerprint_scores
    ),
    quality_map_check AS (
        SELECT 
            es.server_id,
            es.signal_name,
            es.last_error
        FROM mcp_signal_scores es
        WHERE es.signal_name = 'tool_description_safety'
          AND es.last_error LIKE '%failed in cohort_9_n4%'
    )
    SELECT 
        ds.score_count,
        COUNT(DISTINCT qm.server_id) AS cohort_n4_errors,
        (SELECT COUNT(*) FROM enrichment_source) AS total_enriched
    FROM distinct_scores ds
    LEFT JOIN quality_map_check qm ON 1=1
    GROUP BY ds.score_count
    """
    
    try:
        result = ws_query(validation_sql)
        rows = result.get('rows', [])
        
        if not rows:
            report["status"] = "FAIL"
            report["reason"] = "No results returned from validation query"
            report["distinct_scores"] = 0
            return report
        
        row = rows[0]
        distinct_count = row.get('score_count', 0) or 0
        cohort_errors = row.get('cohort_n4_errors', 0) or 0
        total_enriched = row.get('total_enriched', 0) or 0
        
        report["distinct_scores"] = distinct_count
        report["cohort_9_n4_errors"] = cohort_errors
        report["total_enriched_servers"] = total_enriched
        report["threshold"] = 20
        
        if distinct_count >= 20:
            report["status"] = "PASS"
            report["message"] = f"Enrichment evidence gate satisfied: {distinct_count} distinct scores (>= {report['threshold']})"
        else:
            report["status"] = "FAIL"
            report["message"] = f"Enrichment evidence gate failed: {distinct_count} distinct scores (< {report['threshold']})"
            if cohort_errors > 0:
                report["cohort_warning"] = f"{cohort_errors} servers affected by cohort_9_n4 failure"
        
    except Exception as e:
        report["status"] = "ERROR"
        report["error"] = str(e)
        report["message"] = f"Validation query execution failed: {e}"
    
    # Write report to audit log
    report_id = hashlib.md5(
        f"tool_description_enrichment_evidence_{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()[:16]
    
    try:
        audit_sql = f"""
        INSERT INTO audit_log (id, target_server_id, event_type, actor, detail, created_at)
        VALUES (
            '{report_id}',
            'SYSTEM',
            'enrichment_validation',
            'tool_description_enrichment_evidence_check',
            '{json.dumps(report).replace("'", "''")}',
            '{datetime.now(timezone.utc).isoformat()}'
        )
        """
        ws_execute(audit_sql)
    except Exception:
        pass  # Non-blocking audit write
    
    return report

if __name__ == '__main__':
    print(f"[tool_description_enrichment_evidence_check] Running validation at {datetime.now(timezone.utc).isoformat()}")
    report = build_validation_report()
    print(json.dumps(report, indent=2))
    
    if report["status"] == "PASS":
        print("\nRESULT: PASS")
        sys.exit(0)
    elif report["status"] == "FAIL":
        print("\nRESULT: FAIL")
        sys.exit(1)
    else:
        print("\nRESULT: ERROR")
        sys.exit(2)