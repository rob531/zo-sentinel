#!/usr/bin/env python3
"""
diagnose_signal_weakness.py
Investigates why known_bad_pattern (distinct=2) and tool_count (distinct=2) signals 
show WEAK discrimination in mcp_signal_scores. 

Queries the database for these signal types and reports:
- Number of distinct score values
- Value distribution histogram  
- Which MCPs share identical scores

Output: JSON diagnostic to shared/outputs/goose/diagnose_signal_weakness.json

This is observation only - does NOT modify any enrichment files.
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# deps: requests
import requests

# Project paths
PROJECT_ROOT = Path('/home/workspace/zo_sentinel')
OUTPUT_DIR = PROJECT_ROOT / 'shared' / 'outputs' / 'goose'
OUTPUT_FILE = OUTPUT_DIR / 'diagnose_signal_weakness.json'

WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
QUERY_ENDPOINT = f'{WRITE_SERVICE_URL}/query'

# Target signals to investigate
TARGET_SIGNALS = ['known_bad_pattern', 'tool_count']


def ws_query(sql: str, params: list = None) -> list[dict[str, Any]]:
    """Execute a SELECT query via write_service /query endpoint."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            QUERY_ENDPOINT,
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        result = resp.json()
        if isinstance(result, dict):
            return result.get('rows', [])
        elif isinstance(result, list):
            return result
        return []
    except Exception as e:
        print(f"Query failed: {e}", file=sys.stderr)
        return []


def _find_scores_file() -> str:
    """Legacy compatibility - no local file, data comes from database."""
    return str(OUTPUT_FILE)


def _load_scores_dataframe(signal_name: str) -> list[dict[str, Any]]:
    """
    Load scores for a signal from mcp_signal_scores table.
    Returns list of score records with server_id, score, evidence, scored_at.
    """
    sql = f"""
    SELECT 
        s.server_id,
        s.score,
        s.evidence,
        s.scored_at,
        r.name as mcp_name,
        r.registry_source
    FROM mcp_signal_scores s
    LEFT JOIN mcp_server_registry r ON s.server_id = r.server_id
    WHERE s.signal_name = ?
    ORDER BY s.scored_at DESC
    """
    return ws_query(sql, [signal_name])


def _analyze_signal(signal_name: str) -> dict[str, Any]:
    """
    Analyze a single signal for weakness (low cardinality).
    Returns diagnostic dict with stats, distribution, and server clusters.
    """
    rows = _load_scores_dataframe(signal_name)
    
    if not rows:
        return {
            'signal_name': signal_name,
            'error': 'No data found',
            'stats': {}
        }
    
    # Basic stats
    scores = [r['score'] for r in rows if r.get('score') is not None]
    distinct_scores = sorted(set(scores))
    unique_servers = set(r['server_id'] for r in rows)
    
    stats = {
        'signal_name': signal_name,
        'row_count': len(rows),
        'unique_servers': len(unique_servers),
        'distinct_scores': len(distinct_scores),
        'min_score': min(scores) if scores else None,
        'max_score': max(scores) if scores else None,
        'avg_score': sum(scores) / len(scores) if scores else None,
        'median': _median(scores) if scores else None
    }
    
    # Distribution histogram
    score_counts = Counter(scores)
    total = len(scores)
    distribution = []
    for score in sorted(score_counts.keys()):
        count = score_counts[score]
        # Count unique servers for this score
        servers_with_score = set(
            r['server_id'] for r in rows 
            if r.get('score') == score
        )
        pct = (count / total * 100) if total > 0 else 0
        distribution.append({
            'score': score,
            'server_count': count,
            'pct': round(pct, 4),
            'distinct_servers': len(servers_with_score),
            'bar': '█' * min(int(pct / 2), 50)  # Visual bar capped at 50 chars
        })
    
    # Group servers by score to find clusters
    score_buckets = defaultdict(list)
    for r in rows:
        score = r.get('score')
        if score is not None:
            # Truncate evidence for preview
            evidence = r.get('evidence', '')
            if evidence and len(evidence) > 200:
                evidence_preview = evidence[:200] + '...'
            else:
                evidence_preview = evidence
            score_buckets[score].append({
                'server_id': r['server_id'],
                'mcp_name': r.get('mcp_name', r['server_id']),
                'registry_source': r.get('registry_source', 'unknown'),
                'evidence_preview': evidence_preview,
                'scored_at': r.get('scored_at')
            })
    
    # Limit servers per bucket to first 10
    score_buckets_list = []
    for score in sorted(score_buckets.keys()):
        servers = score_buckets[score][:10]
        score_buckets_list.append({
            'score': score,
            'bucket_size': len(score_buckets[score]),
            'servers': servers
        })
    
    # Sample evidence per score bucket
    evidence_samples = []
    for score in sorted(score_counts.keys()):
        evidence_for_score = set(
            r.get('evidence', '') for r in rows 
            if r.get('score') == score and r.get('evidence')
        )
        for ev in list(evidence_for_score)[:3]:
            evidence_samples.append({
                'score': score,
                'evidence': ev,
                'count': sum(1 for r in rows if r.get('score') == score and r.get('evidence') == ev)
            })
    
    # Score clusters (servers with same score)
    score_clusters = []
    for score in sorted(score_buckets.keys()):
        servers = score_buckets[score]
        if len(servers) > 1:
            score_clusters.append({
                'score': score,
                'server_count': len(servers),
                'servers': [s['server_id'] for s in servers[:20]]  # Limit to 20
            })
    
    # Determine verdict
    verdict = 'WEAK' if len(distinct_scores) <= 4 else 'MODERATE' if len(distinct_scores) <= 10 else 'STRONG'
    
    return {
        'signal_name': signal_name,
        'investigated_at': datetime.now(timezone.utc).isoformat(),
        'stats': stats,
        'verdict': verdict,
        'distribution': distribution,
        'score_buckets': score_buckets_list,
        'evidence_samples': evidence_samples[:20],
        'score_clusters': score_clusters
    }


def _median(values: list) -> float:
    """Compute median of a list."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 0:
        return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    return sorted_vals[n // 2]


def _get_all_signal_stats() -> list[dict[str, Any]]:
    """Get distinct score counts for all signals as reference."""
    sql = """
    SELECT 
        signal_name,
        COUNT(*) as row_count,
        COUNT(DISTINCT server_id) as distinct_servers,
        COUNT(DISTINCT score) as distinct_scores,
        MIN(score) as min_score,
        MAX(score) as max_score
    FROM mcp_signal_scores
    GROUP BY signal_name
    ORDER BY distinct_scores ASC
    """
    return ws_query(sql)


def _get_schema_info() -> dict[str, Any]:
    """Get mcp_signal_scores table schema for confirmation."""
    sql = """
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'mcp_signal_scores'
    ORDER BY ordinal_position
    """
    rows = ws_query(sql)
    return {
        'table': 'mcp_signal_scores',
        'columns': [
            {'column_name': r['column_name'], 'data_type': r['data_type']}
            for r in rows
        ]
    }


def main() -> dict[str, Any]:
    """
    Main entry point - run full diagnostic on target signals.
    Returns diagnostic dict and writes JSON to output file.
    """
    print(f"Starting signal weakness diagnosis at {datetime.now(timezone.utc).isoformat()}")
    print(f"Target signals: {TARGET_SIGNALS}")
    
    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Analyze each target signal
    diagnostics = []
    for signal in TARGET_SIGNALS:
        print(f"\nAnalyzing signal: {signal}")
        diag = _analyze_signal(signal)
        diagnostics.append(diag)
        print(f"  Rows: {diag['stats'].get('row_count', 0)}")
        print(f"  Distinct scores: {diag['stats'].get('distinct_scores', 0)}")
        print(f"  Verdict: {diag['verdict']}")
    
    # Get reference stats for all signals
    all_signals = _get_all_signal_stats()
    
    # Build root causes analysis
    root_causes = []
    for diag in diagnostics:
        if diag.get('verdict') == 'WEAK':
            root_causes.append(
                f"Signal: {diag['signal_name']}\n"
                f"Verdict: {diag['verdict']} (distinct_scores={diag['stats'].get('distinct_scores', 0)})\n"
                f"Root cause hypothesis: The scorer produces a near-binary output. "
                f"Check whether the underlying enrichment computes a boolean / binary-threshold "
                f"result rather than a continuous score."
            )
    
    # Build final output
    output = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'description': (
            f"Diagnostic for known_bad_pattern and tool_count signals showing WEAK "
            f"discrimination (distinct=2) in mcp_signal_scores."
        ),
        'targets': TARGET_SIGNALS,
        'signal_diagnostics': diagnostics,
        'reference_all_signals': all_signals,
        'root_causes': root_causes,
        'schema_confirmed': _get_schema_info()
    }
    
    # Write JSON output
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"\nDiagnostic written to: {OUTPUT_FILE}")
    
    # Summary
    print("\n" + "=" * 60)
    print("DIAGNOSIS SUMMARY")
    print("=" * 60)
    for diag in diagnostics:
        stats = diag.get('stats', {})
        print(f"\n{diag['signal_name']}:")
        print(f"  Distinct scores: {stats.get('distinct_scores', 0)}")
        print(f"  Verdict: {diag['verdict']}")
        if diag.get('distribution'):
            for d in diag['distribution'][:5]:
                print(f"    Score {d['score']}: {d['server_count']} rows ({d['pct']:.2f}%)")
    
    if root_causes:
        print("\nROOT CAUSES IDENTIFIED:")
        for rc in root_causes:
            print(f"\n{rc}")
    
    return output


if __name__ == '__main__':
    try:
        result = main()
        print("\nPASS")
        sys.exit(0)
    except Exception as e:
        print(f"\nFAIL: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
