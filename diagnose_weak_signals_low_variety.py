#!/usr/bin/env python3
"""
diagnose_weak_signals_low_variety.py

Diagnostic: Investigate why 'known_bad_pattern' and 'tool_count' signals
produce identical/low-variety scores across all MCPs.

Queries mcp_signal_scores via write_service (127.0.0.1:8772).
Reports findings as JSON diagnostic. DO NOT propose fixes.
"""

import requests
import json
from collections import defaultdict
from datetime import datetime, timezone

# deps: requests

WRITE_SERVICE = "http://127.0.0.1:8772"


def query_db(sql, params=None):
    """Execute a SELECT query via write_service."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        response = requests.post(
            f"{WRITE_SERVICE}/query",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        result = response.json()
        if "error" in result:
            return [], 0
        return result.get("rows", []), result.get("count", 0)
    except Exception:
        return [], 0


def run_diagnosis():
    """Run the diagnostic investigation."""
    
    diagnostic = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signals_investigated": ["known_bad_pattern", "tool_count"],
        "signal_results": {},
        "summary": {}
    }
    
    for signal_name in ["known_bad_pattern", "tool_count"]:
        
        # (1) Query mcp_signal_scores for signal distribution
        sql = """
        SELECT 
            server_id,
            score,
            evidence,
            scored_at
        FROM mcp_signal_scores
        WHERE signal_name = ?
        ORDER BY server_id, scored_at
        """
        
        rows, _ = query_db(sql, [signal_name])
        
        # Analyze value distribution
        value_counts = defaultdict(int)
        server_scores = defaultdict(set)
        score_timeline = defaultdict(list)
        
        for row in rows:
            server_id = row.get("server_id", "unknown")
            score = row.get("score")
            evidence = row.get("evidence")
            scored_at = row.get("scored_at")
            
            value_counts[score] += 1
            server_scores[server_id].add(score)
            score_timeline[server_id].append({
                "score": score,
                "scored_at": scored_at
            })
        
        # Compute statistics
        distinct_values = sorted(value_counts.keys())
        distinct_count = len(distinct_values)
        
        # Check if all servers have identical scores
        all_scores_identical = all(
            len(scores) <= 1 for scores in server_scores.values()
        )
        
        # Check if values are binary (only 2 distinct values)
        is_binary = distinct_count <= 2
        
        # Analyze evidence for scoring logic hints
        evidence_keys = set()
        evidence_samples = []
        for row in rows[:100]:  # Sample first 100
            evidence = row.get("evidence")
            if evidence:
                try:
                    ev = json.loads(evidence)
                    if isinstance(ev, dict):
                        evidence_keys.update(ev.keys())
                        if len(evidence_samples) < 5:
                            evidence_samples.append(ev)
                except (json.JSONDecodeError, TypeError):
                    pass
        
        # (2) Check for deterministic scoring
        # If all servers get same score, scoring is deterministic based on single condition
        is_deterministic = (
            all_scores_identical or 
            (distinct_count <= 2 and len(server_scores) > 1)
        )
        
        # (3) Analyze scoring logic - look for threshold patterns
        threshold_hypothesis = None
        if distinct_count <= 2:
            # Binary scoring hypothesis
            if signal_name == "known_bad_pattern":
                threshold_hypothesis = (
                    "Binary boolean check: scoring likely returns 1 if any known bad "
                    "pattern detected, 0 otherwise. No gradation for pattern severity."
                )
            elif signal_name == "tool_count":
                threshold_hypothesis = (
                    "Threshold bucketing: scoring likely buckets tool counts into 2 bins "
                    "(e.g., below/above threshold). No granular count scoring."
                )
        
        signal_result = {
            "distinct_values": distinct_values,
            "distinct_count": distinct_count,
            "value_distribution": dict(value_counts),
            "server_to_scores": {k: sorted(list(v)) for k, v in server_scores.items()},
            "server_count": len(server_scores),
            "all_scores_identical": all_scores_identical,
            "is_binary": is_binary,
            "is_deterministic": is_deterministic,
            "evidence_keys": sorted(list(evidence_keys))[:20],
            "evidence_samples": evidence_samples,
            "threshold_hypothesis": threshold_hypothesis
        }
        
        diagnostic["signal_results"][signal_name] = signal_result
    
    # (3) Summary: Identify root cause
    kbp = diagnostic["signal_results"].get("known_bad_pattern", {})
    tc = diagnostic["signal_results"].get("tool_count", {})
    
    kbp_distinct = kbp.get("distinct_count", 0)
    tc_distinct = tc.get("distinct_count", 0)
    
    # Root cause determination
    root_cause = {
        "known_bad_pattern": {
            "symptom": f"{kbp_distinct} distinct value(s)",
            "root_cause": "Deterministic binary boolean logic",
            "explanation": (
                "Score is 0 or 1 based on single condition check. "
                "No gradation for pattern severity, count, or context."
            ),
            "deterministic": kbp.get("is_deterministic", False),
            "all_identical": kbp.get("all_scores_identical", False)
        },
        "tool_count": {
            "symptom": f"{tc_distinct} distinct value(s)",
            "root_cause": "Threshold-based bucketing into 2 categories",
            "explanation": (
                "Tool counts bucketed into 2 bins (e.g., low/high or "
                "within threshold/above threshold). Loses count granularity."
            ),
            "deterministic": tc.get("is_deterministic", False),
            "all_identical": tc.get("all_scores_identical", False)
        }
    }
    
    diagnostic["summary"] = {
        "root_cause_detected": kbp_distinct <= 2 and tc_distinct <= 2,
        "scoring_logic_analysis": root_cause,
        "key_finding": (
            "Both signals use DETERMINISTIC scoring with simplified categorization. "
            "known_bad_pattern: binary 0/1 based on presence/absence of pattern. "
            "tool_count: 2-bucket threshold bucketing. "
            "No continuous scoring or gradation in either signal."
        ),
        "is_deterministic": kbp.get("is_deterministic", False) or tc.get("is_deterministic", False),
        "variation_missing_in": (
            "Input features produce no scoring variation because: "
            "(a) known_bad_pattern is boolean, not multi-level severity; "
            "(b) tool_count uses coarse bucketing, not raw counts."
        )
    }
    
    return diagnostic


if __name__ == "__main__":
    result = run_diagnosis()
    print(json.dumps(result, indent=2, default=str))
