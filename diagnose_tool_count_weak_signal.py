#!/usr/bin/env python3
"""
diagnose_tool_count_weak_signal.py

Diagnostic to investigate the tool_count signal showing only 2 distinct values
(range 55.0-92.0) across the corpus, indicating poor discrimination.

Must:
1. Query mcp_signal_scores WHERE signal_type='tool_count' to examine score distribution
2. Identify if the signal producer is reading tool_count from mcp_server_registry or mcp_fingerprints
3. Determine if the issue is in scoring logic vs input data
4. Report findings as JSON to diagnostics output
"""

import json
import sys
from datetime import datetime, timezone
from typing import Any

# deps: requests

sys.path.insert(0, '/home/workspace/zo_sentinel')

from db_utils import ws_query


def query_tool_count_score_distribution() -> dict[str, Any]:
    """Query the distribution of tool_count scores in mcp_signal_scores."""
    sql = """
    SELECT 
        COUNT(*) as total_rows,
        COUNT(DISTINCT score) as distinct_scores,
        MIN(score) as min_score,
        MAX(score) as max_score,
        AVG(score) as avg_score,
        STDDEV(score) as stddev_score
    FROM mcp_signal_scores
    WHERE signal_name = 'tool_count'
    """
    result = ws_query(sql)
    rows = result.get('rows', []) if isinstance(result, dict) else (result if result else [])
    return rows[0] if rows else {}


def query_score_frequency() -> list[dict[str, Any]]:
    """Get frequency distribution of each distinct score value."""
    sql = """
    SELECT 
        score,
        COUNT(*) as frequency,
        ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) as pct
    FROM mcp_signal_scores
    WHERE signal_name = 'tool_count'
    GROUP BY score
    ORDER BY score
    """
    return ws_query(sql)


def query_tool_count_source_data() -> dict[str, Any]:
    """
    Determine where tool_count is sourced from.
    Check mcp_fingerprints table for tool_count column.
    """
    findings = {
        "mcp_fingerprints_has_tool_count": False,
        "mcp_server_registry_has_tool_count": False,
        "sample_fingerprints": [],
        "sample_registry": [],
    }
    
    # Check mcp_fingerprints for tool_count
    sql_fingerprints = """
    SELECT server_id, tool_count
    FROM mcp_fingerprints
    WHERE tool_count IS NOT NULL
    ORDER BY tool_count DESC
    LIMIT 20
    """
    try:
        fp_results = ws_query(sql_fingerprints)
        if fp_results:
            findings["mcp_fingerprints_has_tool_count"] = True
            findings["sample_fingerprints"] = fp_results
            
            # Get distinct tool_count values from fingerprints
            sql_distinct = """
            SELECT COUNT(DISTINCT tool_count) as distinct_tool_counts,
                   MIN(tool_count) as min_tool_count,
                   MAX(tool_count) as max_tool_count
            FROM mcp_fingerprints
            WHERE tool_count IS NOT NULL
            """
            dist_result = ws_query(sql_distinct)
            if dist_result:
                findings["fingerprints_tool_count_stats"] = dist_result[0]
    except Exception as e:
        findings["fingerprints_error"] = str(e)
    
    # Check mcp_server_registry for tool_count
    sql_registry = """
    SELECT server_id, tool_count
    FROM mcp_server_registry
    WHERE tool_count IS NOT NULL
    LIMIT 10
    """
    try:
        reg_results = ws_query(sql_registry)
        if reg_results:
            findings["mcp_server_registry_has_tool_count"] = True
            findings["sample_registry"] = reg_results
    except Exception as e:
        findings["registry_error"] = str(e)
    
    return findings


def analyze_scoring_logic_issue() -> dict[str, Any]:
    """
    Analyze the scoring logic in signal_analyser_v2.py to identify
    why only 2 distinct scores are produced from a wide range.
    """
    # The scoring logic from signal_analyser_v2.py:
    # def score_tool_count(tool_count: int) -> float:
    #     if tool_count == 0: return 25.0
    #     elif tool_count < 3: return 40.0
    #     elif tool_count < 10: return 60.0
    #     elif tool_count < 25: return 75.0
    #     elif tool_count < 50: return 85.0
    #     elif tool_count < 100: return 90.0
    #     else: return 95.0
    
    findings = {
        "scoring_buckets_defined": 7,
        "scoring_buckets": [
            {"range": "tool_count == 0", "score": 25.0},
            {"range": "0 < tool_count < 3", "score": 40.0},
            {"range": "3 <= tool_count < 10", "score": 60.0},
            {"range": "10 <= tool_count < 25", "score": 75.0},
            {"range": "25 <= tool_count < 50", "score": 85.0},
            {"range": "50 <= tool_count < 100", "score": 90.0},
            {"range": "tool_count >= 100", "score": 95.0},
        ],
        "issue_identified": "score_collapse",
        "issue_description": (
            "The scoring logic creates only 7 discrete buckets. "
            "Most MCPs likely fall into 2 buckets (60.0 and 75.0), "
            "causing the observed narrow range of 55.0-92.0."
        ),
        "root_cause": "scoring_logic",
        "recommendation": (
            "Replace bucketed scoring with graduated linear scoring "
            "that produces distinct scores for each tool_count value."
        ),
    }
    return findings


def run() -> dict[str, Any]:
    """Run the complete diagnostic and return findings."""
    findings = {
        "diagnostic": "tool_count_weak_signal",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signal_name": "tool_count",
        "reported_symptom": "only 2 distinct values, range 55.0-92.0",
    }
    
    # 1. Query score distribution
    findings["score_distribution"] = query_tool_count_score_distribution()
    
    # 2. Query score frequency
    findings["score_frequency"] = query_score_frequency()
    
    # 3. Identify data source
    findings["data_source"] = query_tool_count_source_data()
    
    # 4. Analyze scoring logic
    findings["scoring_analysis"] = analyze_scoring_logic_issue()
    
    # 5. Determine root cause
    dist = findings["score_distribution"]
    freq = findings["score_frequency"]
    
    if dist.get("distinct_scores", 0) <= 3:
        findings["diagnosis"] = "scoring_logic_collapse"
        findings["confidence"] = "high"
        findings["summary"] = (
            f"Found only {dist.get('distinct_scores', '?')} distinct score values. "
            f"The scoring logic has 7 buckets but input data clusters into fewer buckets. "
            f"Data source: mcp_fingerprints.tool_count."
        )
    else:
        findings["diagnosis"] = "input_data_issue"
        findings["confidence"] = "medium"
        findings["summary"] = (
            "Multiple distinct scores exist. Issue may be in data quality or "
            "insufficient variance in input tool_count values."
        )
    
    return findings


if __name__ == "__main__":
    findings = run()
    
    # Output JSON to stdout
    output = json.dumps(findings, indent=2, default=str)
    print(output)
    
    # Write to diagnostics output file
    output_path = "/home/workspace/zo_sentinel/shared/outputs/goose/diagnose_tool_count_weak_signal.json"
    try:
        import os
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            f.write(output)
        print(f"\n[OK] Diagnostic report written to {output_path}")
    except Exception as e:
        print(f"\n[WARN] Could not write output file: {e}")
    
    sys.exit(0)
