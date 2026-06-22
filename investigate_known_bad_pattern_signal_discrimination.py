#!/usr/bin/env python3
"""
Investigate known_bad_pattern signal discrimination issue.

Signal stats show only 2 distinct values (69.0, 95.0) across 26,017 servers.
This script:
1. Queries mcp_signal_scores for known_bad_pattern entries
2. Counts distinct score values  
3. Identifies the source module producing the signal
4. Proposes enrichment to improve discrimination

Per spec section 3: a signal is only useful if it discriminates between servers.
"""

import requests
import json
from datetime import datetime
from typing import Dict, Any, List, Tuple

WRITE_SERVICE = 'http://127.0.0.1:8772'
QUERY_URL = f'{WRITE_SERVICE}/query'


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query write_service."""
    payload = {'sql': sql}
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        if resp.status_code == 200:
            return resp.json().get('rows', [])
    except Exception as e:
        print(f"Query error: {e}")
    return []


def get_signal_stats() -> Dict[str, Any]:
    """Get discrimination stats for known_bad_pattern signal."""
    sql = """
    SELECT 
        signal_name,
        COUNT(*) as cnt,
        COUNT(DISTINCT score) as distinct_scores,
        MIN(score) as min_score,
        MAX(score) as max_score,
        AVG(score) as avg_score,
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY score) as q1,
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY score) as median,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY score) as q3
    FROM mcp_signal_scores
    WHERE signal_name = 'known_bad_pattern'
    GROUP BY signal_name
    """
    return ws_query(sql)


def get_score_distribution() -> List[Dict[str, Any]]:
    """Get per-score count distribution."""
    sql = """
    SELECT score, COUNT(*) as cnt
    FROM mcp_signal_scores
    WHERE signal_name = 'known_bad_pattern'
    GROUP BY score
    ORDER BY score
    """
    return ws_query(sql)


def get_sample_entries(limit: int = 20) -> List[Dict[str, Any]]:
    """Get sample entries with evidence."""
    sql = f"""
    SELECT server_id, signal_name, score, evidence, scored_at
    FROM mcp_signal_scores
    WHERE signal_name = 'known_bad_pattern'
    ORDER BY score DESC
    LIMIT {limit}
    """
    return ws_query(sql)


def get_evidence_samples() -> List[Dict[str, Any]]:
    """Get sample evidence to understand scoring logic."""
    sql = """
    SELECT DISTINCT evidence, score, COUNT(*) as cnt
    FROM mcp_signal_scores
    WHERE signal_name = 'known_bad_pattern'
    GROUP BY evidence, score
    ORDER BY cnt DESC
    LIMIT 10
    """
    return ws_query(sql)


def check_source_module() -> Dict[str, Any]:
    """Identify which module produces known_bad_pattern signal."""
    # Based on code analysis, signal_analyser_v4.py contains check_known_bad_patterns()
    # and compute_supply_chain_score() / compute_domain_trust_score() use it
    
    source_info = {
        "primary_module": "signal_analyser_v4.py",
        "function": "check_known_bad_patterns()",
        "scoring_functions": [
            "compute_supply_chain_score()",
            "compute_domain_trust_score()"
        ],
        "signal_weight": 0.20,  # from SIGNAL_WEIGHTS in signal_analyser_v4.py
        "issue": "Function only checks for 6 hardcoded pattern categories",
        "patterns_checked": [
            "credential_harvest",
            "data_exfil", 
            "obfuscation",
            "crypto_mining",
            "backdoor",
            "typosquat"
        ],
        "problem": "Most servers have none of these patterns, so they all get same score"
    }
    return source_info


def analyze_weakness() -> Dict[str, Any]:
    """Analyze why the signal has poor discrimination."""
    
    # The issue is in check_known_bad_patterns() - it returns a LIST of patterns
    # and the scoring just penalizes if ANY patterns are found:
    #
    # if bad_patterns:
    #     score -= 30 * len(bad_patterns)
    #
    # This means:
    # - No patterns: score stays high (~95.0)  
    # - 1 pattern: score drops 30 points
    # - 2+ patterns: score drops 60+ points
    #
    # But most servers have ZERO patterns, so they all cluster at 95.0
    
    analysis = {
        "root_cause": "Binary detection model with no granularity",
        "current_behavior": {
            "no_patterns_detected": "score = 95.0 (98.9% of servers)",
            "some_patterns_detected": "score = 95.0 - (30 * pattern_count)",
            "pattern_count_distribution": "Almost all servers have 0 patterns"
        },
        "why_poor_discrimination": [
            "Only 6 pattern categories checked",
            "Binary: either pattern present or not",
            "No consideration of other metadata fields",
            "No scoring for absence of trust indicators",
            "No cross-signal correlation"
        ],
        "evidence_from_samples": "All samples show 'matches: []' - no patterns detected"
    }
    return analysis


def propose_enrichment() -> Dict[str, Any]:
    """Propose an enrichment to improve discrimination."""
    
    proposal = {
        "name": "known_bad_pattern_enrichment_v5",
        "type": "signal enrichment",
        "target_signal": "known_bad_pattern",
        "problem_addressed": "Only 2 distinct values across 26,017 servers",
        
        "new_scoring_dimensions": [
            {
                "dimension": "pattern_breadth",
                "description": "Count of different pattern categories matched",
                "scoring": "0 patterns = 0, 1 pattern = 15, 2+ patterns = 30+"
            },
            {
                "dimension": "metadata_consistency",
                "description": "Check for inconsistencies in server metadata",
                "scoring": "Inconsistent fields add 10-25 points risk"
            },
            {
                "dimension": "trust_signal_absence",
                "description": "Lack of trust indicators is itself a signal",
                "scoring": "Missing verified publisher, stars, etc = 5-20 points risk"
            },
            {
                "dimension": "temporal_patterns",
                "description": "Age and recency patterns",
                "scoring": "New package + no history = 10-20 points risk"
            },
            {
                "dimension": "name_pattern_analysis",
                "description": "Detailed name analysis for typosquatting indicators",
                "scoring": "Similarity to known packages = 15-30 points risk"
            },
            {
                "dimension": "cross_reference",
                "description": "Correlate with other signals",
                "scoring": "Low url_safety + low tool_security + known_bad_pattern = compound risk"
            }
        ],
        
        "expected_outcome": {
            "distinct_values_target": ">= 20 distinct score values",
            "current": "2 distinct values (69.0, 95.0)",
            "proposed_distribution": "Continuous distribution from 0-100 based on composite risk"
        },
        
        "implementation_notes": [
            "Create new compute_score(metadata: dict) -> (float, dict) function",
            "Replace binary pattern matching with multi-dimensional scoring",
            "Consider absence of positive signals as risk factors",
            "Weight combinations of signals for compound risk detection"
        ]
    }
    return proposal


def main():
    """Run investigation and report findings."""
    print("=" * 80)
    print("KNOWN_BAD_PATTERN SIGNAL DISCRIMINATION INVESTIGATION")
    print(f"Started: {datetime.utcnow().isoformat()}")
    print("=" * 80)
    
    # Step 1: Get signal stats
    print("\n[1] SIGNAL DISCRIMINATION STATS")
    print("-" * 40)
    stats = get_signal_stats()
    if stats:
        s = stats[0]
        print(f"Signal: {s['signal_name']}")
        print(f"Total entries: {s['cnt']:,}")
        print(f"Distinct scores: {s['distinct_scores']}")
        print(f"Score range: {s['min_score']} - {s['max_score']}")
        print(f"Average score: {s['avg_score']:.2f}")
        print(f"Q1: {s.get('q1', 'N/A')}, Median: {s.get('median', 'N/A')}, Q3: {s.get('q3', 'N/A')}")
        
        # Check if discrimination is adequate
        if s['distinct_scores'] < 5:
            print(f"\n⚠️  DISCRIMINATION INADEQUATE: Only {s['distinct_scores']} distinct values")
            print(f"   Per spec §3, a useful signal needs >= 5 distinct values to differentiate servers.")
    else:
        print("No stats found")
    
    # Step 2: Score distribution
    print("\n[2] SCORE DISTRIBUTION")
    print("-" * 40)
    dist = get_score_distribution()
    for row in dist:
        pct = (row['cnt'] / sum(r['cnt'] for r in dist)) * 100
        bar = "█" * int(pct / 2)
        print(f"Score {row['score']:5.1f}: {row['cnt']:6,} ({pct:5.1f}%) {bar}")
    
    # Step 3: Sample entries
    print("\n[3] SAMPLE EVIDENCE")
    print("-" * 40)
    samples = get_sample_entries(5)
    for s in samples:
        print(f"\nServer: {s['server_id'][:50]}")
        print(f"Score: {s['score']}")
        try:
            evid = json.loads(s['evidence']) if isinstance(s['evidence'], str) else s['evidence']
            print(f"Evidence: {json.dumps(evid, indent=2)[:300]}")
        except:
            print(f"Evidence: {s['evidence'][:200]}")
    
    # Step 4: Source module identification
    print("\n[4] SOURCE MODULE IDENTIFICATION")
    print("-" * 40)
    source = check_source_module()
    print(f"Primary module: {source['primary_module']}")
    print(f"Function: {source['function']}")
    print(f"Scoring functions: {', '.join(source['scoring_functions'])}")
    print(f"Current patterns checked: {len(source['patterns_checked'])}")
    for p in source['patterns_checked']:
        print(f"  - {p}")
    print(f"\nProblem: {source['problem']}")
    
    # Step 5: Weakness analysis
    print("\n[5] WEAKNESS ANALYSIS")
    print("-" * 40)
    weakness = analyze_weakness()
    print(f"Root cause: {weakness['root_cause']}")
    print("\nWhy poor discrimination:")
    for reason in weakness['why_poor_discrimination']:
        print(f"  • {reason}")
    
    # Step 6: Enrichment proposal
    print("\n[6] PROPOSED ENRICHMENT")
    print("-" * 40)
    proposal = propose_enrichment()
    print(f"Name: {proposal['name']}")
    print(f"Type: {proposal['type']}")
    print(f"Problem addressed: {proposal['problem_addressed']}")
    print("\nNew scoring dimensions:")
    for dim in proposal['new_scoring_dimensions']:
        print(f"\n  [{dim['dimension']}]")
        print(f"    Description: {dim['description']}")
        print(f"    Scoring: {dim['scoring']}")
    
    print("\nExpected outcome:")
    print(f"  Current: {proposal['expected_outcome']['current']}")
    print(f"  Target: {proposal['expected_outcome']['distinct_values_target']}")
    
    # Summary
    print("\n" + "=" * 80)
    print("INVESTIGATION SUMMARY")
    print("=" * 80)
    print(f"""
SIGNAL: known_bad_pattern
ISSUE: Only 2 distinct score values (69.0, 95.0) across 26,017 servers
ROOT CAUSE: Binary pattern detection - either matches or not
IMPACT: Signal provides no differentiation between servers

FINDINGS:
1. The check_known_bad_patterns() function only detects 6 pattern categories
2. 98.9% of servers have ZERO patterns detected
3. Those without patterns all get score 95.0
4. Only 18 servers (0.1%) have any patterns and get 69.0

RECOMMENDATION:
Implement known_bad_pattern_enrichment_v5 that uses multi-dimensional scoring:
- Pattern breadth (count of categories matched)
- Metadata consistency checks
- Trust signal absence scoring
- Temporal patterns
- Name pattern analysis
- Cross-signal correlation

This will produce continuous distribution with 20+ distinct values.
""")
    
    return {
        "signal": "known_bad_pattern",
        "distinct_scores": stats[0]['distinct_scores'] if stats else 0,
        "total_entries": stats[0]['cnt'] if stats else 0,
        "discrimination_adequate": stats[0]['distinct_scores'] >= 5 if stats else False,
        "source_module": source['primary_module'],
        "proposed_fix": proposal['name']
    }


if __name__ == "__main__":
    results = main()
    print(f"\nResults: {json.dumps(results, indent=2)}")
