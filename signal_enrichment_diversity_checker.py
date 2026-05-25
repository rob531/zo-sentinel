#!/usr/bin/env python3
"""
Signal Enrichment Diversity Checker
Diagnostic tool to evaluate signal discrimination power across MCP servers.
"""

import requests
from collections import defaultdict

WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"

THRESHOLD = 20

# The 8 signals tracked in mcp_signal_scores
SIGNALS = [
    "permission_scope",
    "temporal_stability",
    "tool_description_safety",
    "api_surface_complexity",
    "version_specificity",
    "auth_pattern_density",
    "endpoint_entropy",
    "dependency_footprint"
]

def query_service(sql: str) -> dict:
    """Execute read-only query via write_service HTTP endpoint."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"sql": sql},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Query error: {e}")
        return {"rows": [], "count": 0}


def get_all_signals_in_table() -> set:
    """Fetch all signal names currently in the signal_scores table."""
    sql = "SELECT DISTINCT signal_name FROM mcp_signal_scores"
    result = query_service(sql)
    if result and result.get("rows"):
        return {row["signal_name"] for row in result["rows"]}
    return set()


def count_distinct_scores(signal_name: str) -> int:
    """Count distinct score values for a given signal."""
    sql = f"SELECT COUNT(DISTINCT score) as distinct_count FROM mcp_signal_scores WHERE signal_name = '{signal_name}'"
    result = query_service(sql)
    if result and result.get("rows"):
        return result["rows"][0].get("distinct_count", 0)
    return 0


def count_total_servers_with_signal(signal_name: str) -> int:
    """Count distinct servers that have scores for this signal."""
    sql = f"SELECT COUNT(DISTINCT server_id) as server_count FROM mcp_signal_scores WHERE signal_name = '{signal_name}'"
    result = query_service(sql)
    if result and result.get("rows"):
        return result["rows"][0].get("server_count", 0)
    return 0


def get_score_distribution(signal_name: str) -> list:
    """Get list of distinct scores and their frequencies."""
    sql = f"SELECT score, COUNT(*) as freq FROM mcp_signal_scores WHERE signal_name = '{signal_name}' GROUP BY score ORDER BY freq DESC"
    result = query_service(sql)
    if result and result.get("rows"):
        return result["rows"]
    return []


def generate_recommendation(signal_name: str, distinct_count: int, server_count: int) -> str:
    """Generate actionable recommendation based on diversity analysis."""
    if distinct_count == 0:
        return f"Investigation required: {signal_name} has no data in signal_scores table"
    
    if distinct_count == 1:
        return f"Critical: Single value across {server_count} servers. Rebuild enrichment logic with more granular scoring. Check wiring to {signal_name}_v2 module."
    
    if distinct_count < 5:
        return f"Poor: Only {distinct_count} distinct values for {server_count} servers. Add more scoring dimensions or integrate with existing {signal_name}_v2 enricher."
    
    if distinct_count < THRESHOLD:
        deficit = THRESHOLD - distinct_count
        return f"Weak: {distinct_count} distinct values (need ~{deficit} more for good discrimination). Consider wiring to {signal_name}_v2 or enhance scoring logic."
    
    return f"Acceptable: {distinct_count} distinct values provide adequate discrimination across {server_count} servers."


def analyze_signal_diversity() -> dict:
    """Main analysis: check all 8 signals for diversity metrics."""
    results = {}
    
    # First, check what signals are actually in the table
    present_signals = get_all_signals_in_table()
    missing_signals = [s for s in SIGNALS if s not in present_signals]
    
    for signal in SIGNALS:
        if signal not in present_signals:
            results[signal] = {
                "signal_type": signal,
                "distinct_count": 0,
                "verdict": "MISSING",
                "recommended_action": f"Signal '{signal}' not found in mcp_signal_scores. Verify enrichment daemon is populating this signal."
            }
            continue
        
        distinct_count = count_distinct_scores(signal)
        server_count = count_total_servers_with_signal(signal)
        
        verdict = "WEAK" if distinct_count < THRESHOLD else "OK"
        recommendation = generate_recommendation(signal, distinct_count, server_count)
        
        results[signal] = {
            "signal_type": signal,
            "distinct_count": distinct_count,
            "verdict": verdict,
            "recommended_action": recommendation
        }
    
    return results, missing_signals


def print_human_summary(results: dict, missing_signals: list):
    """Print human-readable summary of diversity analysis."""
    print("\n" + "=" * 70)
    print("  SIGNAL ENRICHMENT DIVERSITY REPORT")
    print("  ZO-SENTINEL Diagnostic Tool")
    print("=" * 70)
    print(f"\n  Analysis: {len(SIGNALS)} signals checked against threshold of {THRESHOLD} distinct values")
    print("-" * 70)
    
    weak_count = 0
    ok_count = 0
    missing_count = len(missing_signals)
    
    for signal, data in results.items():
        verdict = data["verdict"]
        count = data["distinct_count"]
        
        if verdict == "OK":
            status_icon = "[+]"
            status_color = "ACCEPTABLE"
            ok_count += 1
        elif verdict == "WEAK":
            status_icon = "[-]"
            status_color = "WEAK"
            weak_count += 1
        else:
            status_icon = "[!]"
            status_color = "MISSING"
        
        print(f"\n  {status_icon} {signal}")
        print(f"      Distinct values: {count}")
        print(f"      Verdict: {status_color}")
        print(f"      Recommendation: {data['recommended_action']}")
    
    print("\n" + "-" * 70)
    print("  SUMMARY")
    print("-" * 70)
    print(f"  Acceptable signals (>= {THRESHOLD} distinct): {ok_count}")
    print(f"  Weak signals (< {THRESHOLD} distinct):        {weak_count}")
    print(f"  Missing signals (not in table):              {missing_count}")
    
    if weak_count > 0:
        weak_signals = [s for s, d in results.items() if d["verdict"] == "WEAK"]
        print(f"\n  PRIMARY TARGETS FOR REMEDIATION:")
        for ws in weak_signals:
            print(f"    - {ws}: {results[ws]['distinct_count']} distinct values")
    
    if missing_count > 0:
        print(f"\n  UNPOPULATED SIGNALS:")
        for ms in missing_signals:
            print(f"    - {ms}")
    
    print("\n" + "=" * 70)
    print("  END OF REPORT")
    print("=" * 70 + "\n")


def main():
    print("Starting Signal Enrichment Diversity Checker...")
    print("Querying write_service at", WRITE_SERVICE_URL)
    
    results, missing_signals = analyze_signal_diversity()
    
    # Print human-readable summary
    print_human_summary(results, missing_signals)
    
    # Output structured results as JSON for programmatic consumption
    print("\nStructured Results (JSON):")
    output = {
        "analysis_timestamp": "now",
        "threshold_used": THRESHOLD,
        "signals_analyzed": len(SIGNALS),
        "weak_count": len([s for s, d in results.items() if d["verdict"] == "WEAK"]),
        "ok_count": len([s for s, d in results.items() if d["verdict"] == "OK"]),
        "missing_signals": missing_signals,
        "results": list(results.values())
    }
    print(output)


if __name__ == "__main__":
    main()