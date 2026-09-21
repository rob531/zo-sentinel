#!/usr/bin/env python3
"""
investigate_known_bad_pattern_signal_discrimination_v2.py

Investigate why known_bad_pattern signal has only 2 distinct values (69.0-95.0)
across 1784 servers, indicating poor discrimination.

Queries mcp_signal_scores for actual distribution, identifies which MCPs receive
each value, and determines if the issue is in signal production (mcp_scanner) or
downstream processing.

All DB access via write_service HTTP API (127.0.0.1:8772).
"""

import json
import requests
from datetime import datetime
from typing import Any

WRITE_SERVICE = "http://127.0.0.1:8772"


def ws_query(sql: str) -> list[dict[str, Any]]:
    """Query write_service."""
    payload = {"sql": sql}
    try:
        resp = requests.post(f"{WRITE_SERVICE}/query", json=payload, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("rows", [])
        else:
            print(f"Query error {resp.status_code}: {sql[:100]}")
    except Exception as e:
        print(f"Query failed: {e}")
    return []


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service."""
    payload = {"sql": sql, "wait": True}
    try:
        resp = requests.post(f"{WRITE_SERVICE}/execute", json=payload, timeout=30)
        return resp.status_code == 200
    except Exception as e:
        print(f"Execute failed: {e}")
        return False


def investigate_distribution() -> dict[str, Any]:
    """Get distinct score distribution for known_bad_pattern."""
    sql = """
    SELECT 
        score,
        COUNT(*) as cnt,
        COUNT(DISTINCT server_id) as servers
    FROM mcp_signal_scores
    WHERE signal_name = 'known_bad_pattern'
    GROUP BY score
    ORDER BY score DESC
    """
    return ws_query(sql)


def investigate_score_stats() -> dict[str, Any]:
    """Get overall stats for the signal."""
    sql = """
    SELECT 
        signal_name,
        COUNT(*) as total_rows,
        COUNT(DISTINCT server_id) as unique_servers,
        COUNT(DISTINCT score) as distinct_scores,
        MIN(score) as min_score,
        MAX(score) as max_score,
        AVG(score) as avg_score,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY score) as median
    FROM mcp_signal_scores
    WHERE signal_name = 'known_bad_pattern'
    GROUP BY signal_name
    """
    rows = ws_query(sql)
    return rows[0] if rows else {}


def investigate_servers_by_score() -> dict[str, Any]:
    """Identify which MCPs/servers get each score value."""
    sql = """
    SELECT 
        s.score,
        s.server_id,
        s.evidence,
        s.scored_at,
        r.mcp_name,
        r.registry_source,
        r.submitted_at
    FROM mcp_signal_scores s
    LEFT JOIN mcp_server_registry r ON s.server_id = r.server_id
    WHERE s.signal_name = 'known_bad_pattern'
    ORDER BY s.score DESC, s.server_id
    LIMIT 50
    """
    return ws_query(sql)


def investigate_evidence_samples() -> dict[str, Any]:
    """Sample evidence to understand scoring logic."""
    sql = """
    SELECT 
        DISTINCT score,
        evidence,
        COUNT(*) as cnt
    FROM mcp_signal_scores
    WHERE signal_name = 'known_bad_pattern'
    GROUP BY score, evidence
    ORDER BY score DESC, cnt DESC
    LIMIT 10
    """
    return ws_query(sql)


def investigate_which_mcp_scores() -> dict[str, Any]:
    """Find servers with highest/lowest scores."""
    sql = """
    SELECT 
        score,
        COUNT(*) as cnt
    FROM mcp_signal_scores
    WHERE signal_name = 'known_bad_pattern'
    GROUP BY score
    ORDER BY score DESC
    """
    return ws_query(sql)


def investigate_enrichment_production() -> dict[str, Any]:
    """Check if known_bad_pattern is produced by mcp_scanner or signal_analyser."""
    # First check what modules produce this signal by looking at mcp_signal_enrichments
    sql = """
    SELECT 
        signal_type,
        COUNT(*) as cnt,
        MIN(computed_at) as first_computed,
        MAX(computed_at) as last_computed
    FROM mcp_signal_enrichments
    WHERE signal_type = 'known_bad_pattern_enrichment_v4'
       OR signal_type LIKE 'known_bad_pattern%'
    GROUP BY signal_type
    """
    return ws_query(sql)


def investigate_scanner_production() -> dict[str, Any]:
    """Check mcp_scanner directly for known_bad_pattern production."""
    sql = """
    SELECT 
        server_id,
        name,
        raw_metadata,
        scanned_at
    FROM mcp_scanner
    WHERE name LIKE '%test%' OR name LIKE '%mock%' OR name LIKE '%suspicious%'
    LIMIT 10
    """
    return ws_query(sql)


def investigate_signal_bridge() -> dict[str, Any]:
    """Check if known_bad_pattern comes through signal_bridge."""
    # known_bad_pattern is NOT in ENRICHMENT_TO_SIGNAL mapping in signal_bridge
    # So it might be produced directly by signal_analyser_v4
    sql = """
    SELECT 
        signal_name,
        COUNT(*) as cnt
    FROM mcp_signal_scores
    WHERE signal_name LIKE '%known_bad%'
    GROUP BY signal_name
    """
    return ws_query(sql)


def investigate_mcp_submissions() -> dict[str, Any]:
    """Check submissions for pattern indicators."""
    sql = """
    SELECT 
        server_id,
        mcp_name,
        registry_source,
        submitted_at,
        name_similarity_score
    FROM mcp_submissions
    WHERE name_similarity_score IS NOT NULL
       OR registry_source = 'npm'
    LIMIT 20
    """
    return ws_query(sql)


def investigate_if_issue_in_analyser() -> dict[str, Any]:
    """Determine if issue is in signal_analyser_v4 check_known_bad_patterns()."""
    # Check what score values are produced by looking at recent scoring
    sql = """
    SELECT 
        score,
        scored_at,
        server_id,
        LEFT(evidence, 200) as evidence_preview
    FROM mcp_signal_scores
    WHERE signal_name = 'known_bad_pattern'
    ORDER BY scored_at DESC
    LIMIT 20
    """
    return ws_query(sql)


def main():
    print("=" * 80)
    print("KNOWN_BAD_PATTERN SIGNAL DISCRIMINATION INVESTIGATION v2")
    print(f"Started: {datetime.utcnow().isoformat()}")
    print("=" * 80)

    findings = {}

    # Step 1: Overall stats
    print("\n[1] SIGNAL OVERALL STATS")
    print("-" * 40)
    stats = investigate_score_stats()
    if stats:
        print(f"Signal: {stats.get('signal_name')}")
        print(f"Total rows: {stats.get('total_rows'):,}")
        print(f"Unique servers: {stats.get('unique_servers'):,}")
        print(f"Distinct scores: {stats.get('distinct_scores')}")
        print(f"Score range: {stats.get('min_score')} - {stats.get('max_score')}")
        print(f"Average score: {stats.get('avg_score', 0):.2f}")
        print(f"Median score: {stats.get('median', 0):.2f}")
        findings["stats"] = stats

        # Check discrimination adequacy
        if stats.get("distinct_scores", 0) < 5:
            print(f"\n⚠️  DISCRIMINATION INADEQUATE: Only {stats.get('distinct_scores')} distinct values")
    else:
        print("No stats found - signal may not exist yet")
        findings["stats"] = {"error": "no data"}

    # Step 2: Score distribution
    print("\n[2] SCORE VALUE DISTRIBUTION")
    print("-" * 40)
    dist = investigate_distribution()
    if dist:
        total = sum(r["cnt"] for r in dist)
        for row in dist:
            pct = (row["cnt"] / total) * 100 if total else 0
            bar = "█" * int(pct / 2)
            print(f"Score {row['score']:5.1f}: {row['cnt']:6,} servers ({pct:5.1f}%) {bar}")
        findings["distribution"] = dist
    else:
        print("No distribution data found")

    # Step 3: Which MCPs get each score
    print("\n[3] SAMPLE SERVERS BY SCORE")
    print("-" * 40)
    servers = investigate_servers_by_score()
    current_score = None
    for s in servers[:20]:
        if s["score"] != current_score:
            current_score = s["score"]
            print(f"\n=== Score {current_score} ===")
        print(f"  Server: {s['server_id'][:40]}")
        print(f"  MCP: {s.get('mcp_name', 'N/A')}")
        print(f"  Registry: {s.get('registry_source', 'N/A')}")
        try:
            evid = json.loads(s["evidence"]) if s["evidence"] else {}
            print(f"  Evidence: {json.dumps(evid)[:150]}")
        except:
            print(f"  Evidence: {str(s.get('evidence', ''))[:150]}")
        print()
    findings["servers"] = servers[:5]

    # Step 4: Evidence samples
    print("\n[4] EVIDENCE PATTERNS")
    print("-" * 40)
    evidence = investigate_evidence_samples()
    for e in evidence[:5]:
        print(f"\nScore {e['score']}:")
        print(f"  Count: {e['cnt']}")
        try:
            evid = json.loads(e["evidence"]) if e["evidence"] else {}
            print(f"  Evidence: {json.dumps(evid)[:200]}")
        except:
            print(f"  Evidence: {str(e.get('evidence', ''))[:200]}")

    # Step 5: Check production source
    print("\n[5] SIGNAL PRODUCTION SOURCE")
    print("-" * 40)

    # Check mcp_signal_enrichments for known_bad_pattern production
    enrichments = investigate_enrichment_production()
    if enrichments:
        print("Produced via enrichments:")
        for e in enrichments:
            print(f"  {e['signal_type']}: {e['cnt']} rows")
    else:
        print("No known_bad_pattern enrichments found in mcp_signal_enrichments")

    # Check if signal_bridge handles it
    bridge_signals = investigate_signal_bridge()
    if bridge_signals:
        print("\nIn mcp_signal_scores:")
        for s in bridge_signals:
            print(f"  {s['signal_name']}: {s['cnt']} rows")

    # Step 6: Analyze recent entries
    print("\n[6] RECENT SCORING ENTRIES")
    print("-" * 40)
    recent = investigate_if_issue_in_analyser()
    for r in recent[:5]:
        print(f"\nServer: {r['server_id'][:40]}")
        print(f"  Score: {r['score']}")
        print(f"  At: {r['scored_at']}")
        print(f"  Evidence: {r.get('evidence_preview', 'N/A')[:100]}")

    # Step 7: Root cause determination
    print("\n" + "=" * 80)
    print("ROOT CAUSE ANALYSIS")
    print("=" * 80)

    if dist and len(dist) <= 2:
        print("""
FINDING: Signal has severely limited discrimination (only 2 score values).

POTENTIAL CAUSES:
1. mcp_scanner produces flat raw data (all servers look similar)
2. signal_analyser_v4's check_known_bad_patterns() uses binary logic
3. known_bad_pattern_enrichment_v4 is NOT wired into signal_analyser_v2
4. Signal bridge doesn't carry known_bad_pattern enrichment

RECOMMENDED ACTIONS:
1. Verify if known_bad_pattern_enrichment_v4 is called from signal_analyser_v2
2. Check if check_known_bad_patterns() in signal_analyser_v4 has binary logic
3. Consider wiring v4 enrichment into signal_analyser_v2 for better discrimination
""")
        findings["root_cause"] = "binary_detection_or_unwired_enrichment"
    else:
        print(f"""
FINDING: Signal has {len(dist) if dist else 0} distinct values.
Distribution appears adequate for discrimination.
""")
        findings["root_cause"] = "unknown"

    findings["timestamp"] = datetime.utcnow().isoformat()
    return findings


if __name__ == "__main__":
    results = main()
    print("\n" + "=" * 80)
    print("INVESTIGATION COMPLETE")
    print("=" * 80)
    print(json.dumps(results, indent=2, default=str))
