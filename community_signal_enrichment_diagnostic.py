#!/usr/bin/env python3
"""Diagnostic: Verify community_signal_enrichment.py produces distinct scores."""

import requests
import sys
from datetime import datetime

SERVICE_NAME = "community_signal_enrichment_diagnostic"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"
MIN_DISTINCT_SCORES = 20


def query_db(sql: str) -> dict:
    """Execute SQL query against write_service."""
    response = requests.post(
        WRITE_SERVICE_URL,
        json={"sql": sql},
        headers={"Content-Type": "application/json"},
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def run_diagnostic() -> dict:
    """Run the community signal enrichment diagnostic."""
    findings = {
        "service": SERVICE_NAME,
        "timestamp": datetime.utcnow().isoformat(),
        "status": "unknown",
        "distinct_score_count": 0,
        "score_range": {"min": None, "max": None},
        "total_records": 0,
        "sample_scores": [],
        "errors": [],
        "assertion_passed": False
    }

    try:
        sql = """
        SELECT 
            score,
            COUNT(*) as count
        FROM mcp_signal_enrichments
        WHERE signal_type = 'community_signal'
        GROUP BY score
        ORDER BY score
        """
        
        result = query_db(sql)
        rows = result.get("rows", [])
        
        if not rows:
            findings["status"] = "warning"
            findings["errors"].append("No community_signal records found in mcp_signal_enrichments")
            return findings
        
        findings["total_records"] = len(rows)
        findings["distinct_score_count"] = len(rows)
        
        scores = [row[0] for row in rows if row[0] is not None]
        
        if scores:
            findings["score_range"]["min"] = min(scores)
            findings["score_range"]["max"] = max(scores)
            findings["sample_scores"] = scores[:10] if len(scores) > 10 else scores
        
        findings["assertion_passed"] = findings["distinct_score_count"] >= MIN_DISTINCT_SCORES
        
        if findings["assertion_passed"]:
            findings["status"] = "pass"
        else:
            findings["status"] = "fail"
            findings["errors"].append(
                f"Only {findings['distinct_score_count']} distinct scores found. "
                f"Expected >= {MIN_DISTINCT_SCORES}."
            )
        
    except requests.exceptions.RequestException as e:
        findings["status"] = "error"
        findings["errors"].append(f"Database query failed: {str(e)}")
    except Exception as e:
        findings["status"] = "error"
        findings["errors"].append(f"Unexpected error: {str(e)}")
    
    return findings


def main():
    """Main entry point."""
    print(f"[{datetime.utcnow().isoformat()}] {SERVICE_NAME}: Starting diagnostic...")
    
    findings = run_diagnostic()
    
    print(f"\n{'='*60}")
    print(f"COMMUNITY SIGNAL ENRICHMENT DIAGNOSTIC REPORT")
    print(f"{'='*60}")
    print(f"Status: {findings['status'].upper()}")
    print(f"Timestamp: {findings['timestamp']}")
    print(f"Total Records: {findings['total_records']}")
    print(f"Distinct Score Count: {findings['distinct_score_count']}")
    print(f"Score Range: [{findings['score_range']['min']}, {findings['score_range']['max']}]")
    print(f"Sample Scores: {findings['sample_scores']}")
    print(f"Assertion (>= {MIN_DISTINCT_SCORES} distinct): {'PASS' if findings['assertion_passed'] else 'FAIL'}")
    
    if findings["errors"]:
        print(f"\nErrors:")
        for error in findings["errors"]:
            print(f"  - {error}")
    
    print(f"{'='*60}\n")
    
    if findings["status"] == "pass":
        print("DIAGNOSTIC RESULT: PASS - community_signal_enrichment is producing distinct scores.")
        sys.exit(0)
    elif findings["status"] == "fail":
        print("DIAGNOSTIC RESULT: FAIL - Insufficient distinct score values.")
        sys.exit(1)
    else:
        print(f"DIAGNOSTIC RESULT: {findings['status'].upper()}")
        sys.exit(2)


if __name__ == "__main__":
    main()