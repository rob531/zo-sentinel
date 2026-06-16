#!/usr/bin/env python3
# deps: requests
"""
Enrichment Coverage Diagnostic Utility

Diagnostic utility to audit enrichment pipeline coverage across the MCP corpus.
Identifies which of the 8 signal enrichers are under-performing or producing
insufficient scores, targeting the mcp_signal_enrichments table.

PURPOSE: Diagnose coverage gap between ~1700 MCPs in registry and enrichment rows.
"""

import sys
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import requests

SERVICE_NAME = "enrichment_coverage_diagnostic"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
)
logger = logging.getLogger(__name__)


def ws_query(sql: str, params: list = None) -> list:
    """Query via write_service read endpoint.
    
    Args:
        sql: SQL query string (user values as $1, $2 placeholders)
        params: List of parameter values
        
    Returns:
        List of result rows as dicts
    """
    payload = {"sql": sql, "params": params if params else []}
    resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(f"Query error: {result['error']}")
    return result if isinstance(result, list) else []


def get_total_mcp_count() -> int:
    """Get total count of MCPs in registry."""
    sql = "SELECT COUNT(*) as cnt FROM mcp_server_registry"
    rows = ws_query(sql)
    return rows[0]['cnt'] if rows else 0


def get_enrichment_signal_types() -> list:
    """Get distinct signal types from enrichments table."""
    sql = "SELECT DISTINCT signal_type FROM mcp_signal_enrichments WHERE signal_type IS NOT NULL"
    rows = ws_query(sql)
    return [r['signal_type'] for r in rows]


def get_enrichment_counts_by_signal() -> dict:
    """Get count of enriched MCPs per signal type."""
    sql = """
        SELECT signal_type, COUNT(DISTINCT server_id) as enriched_count
        FROM mcp_signal_enrichments
        WHERE signal_type IS NOT NULL AND server_id IS NOT NULL
        GROUP BY signal_type
    """
    rows = ws_query(sql)
    return {r['signal_type']: r['enriched_count'] for r in rows}


def get_zero_score_mcps() -> list:
    """Find MCPs that have no enrichment scores at all.
    
    Returns server_ids of MCPs in registry with no enrichment entries.
    """
    sql = """
        SELECT r.server_id, r.name
        FROM mcp_server_registry r
        LEFT JOIN mcp_signal_enrichments e ON r.server_id = e.server_id
        WHERE e.server_id IS NULL
        ORDER BY r.first_seen DESC NULLS LAST
        LIMIT 50
    """
    rows = ws_query(sql)
    return [r['server_id'] for r in rows]


def get_stale_enrichments(days_threshold: int = 7) -> list:
    """Find MCPs with enrichments older than specified days."""
    threshold_date = datetime.now(timezone.utc) - timedelta(days=days_threshold)
    threshold_str = threshold_date.isoformat()
    
    sql = """
        SELECT DISTINCT server_id
        FROM mcp_signal_enrichments
        WHERE computed_at < CAST(? AS TIMESTAMPTZ)
        LIMIT 50
    """
    rows = ws_query(sql, params=[threshold_str])
    return [r['server_id'] for r in rows if r.get('server_id')]


def compute_coverage_pct(enriched: int, total: int) -> float:
    """Compute coverage percentage."""
    if total == 0:
        return 0.0
    return round((enriched / total) * 100, 2)


def run_enrichment_coverage_diagnostic() -> dict:
    """Run enrichment coverage diagnostic.
    
    Returns:
        {
            'signal_coverage': {signal_type: {'total_mcps': int, 'enriched': int, 'pct': float}},
            'gap_signals': [signal_type, ...],   # signals with <10% coverage
            'zero_score_mcps': [mcp_identifier, ...],  # MCPs with no enrichment scores
            'stale_enrichments': [mcp_identifier, ...],  # enriched but older than 7 days
        }
    """
    logger.info("=" * 60)
    logger.info("ENRICHMENT COVERAGE DIAGNOSTIC")
    logger.info("=" * 60)
    
    # Get total MCP count from registry
    total_mcps = get_total_mcp_count()
    logger.info(f"Total MCPs in registry: {total_mcps}")
    
    # Get enrichment counts by signal type
    enrichment_counts = get_enrichment_counts_by_signal()
    logger.info(f"Signal types found in enrichments: {list(enrichment_counts.keys())}")
    
    # Build signal coverage dict
    signal_coverage = {}
    all_signal_types = set(enrichment_counts.keys())
    
    # Define the 8 expected signal enricher types
    expected_signals = [
        'evidence_density',
        'ecosystem_relevance', 
        'attestation_quality',
        'fingerprint_diversity',
        'github_velocity',
        'directory_presence',
        'ecosystem_metadata',
        'submission_quality',
    ]
    
    # Add expected signals that may not have any enrichments yet
    for sig in expected_signals:
        if sig not in all_signal_types:
            all_signal_types.add(sig)
    
    for signal_type in sorted(all_signal_types):
        enriched = enrichment_counts.get(signal_type, 0)
        pct = compute_coverage_pct(enriched, total_mcps)
        signal_coverage[signal_type] = {
            'total_mcps': total_mcps,
            'enriched': enriched,
            'pct': pct,
        }
        logger.info(f"  {signal_type}: {enriched}/{total_mcps} ({pct}%)")
    
    # Identify gap signals (<10% coverage)
    gap_signals = [
        sig for sig, data in signal_coverage.items()
        if data['pct'] < 10.0
    ]
    logger.info(f"\nGap signals (<10% coverage): {gap_signals}")
    
    # Get MCPs with zero scores
    zero_score_mcps = get_zero_score_mcps()
    logger.info(f"\nMCPs with zero enrichment scores (showing top 10): {zero_score_mcps[:10]}")
    
    # Get stale enrichments
    stale_enrichments = get_stale_enrichments(days_threshold=7)
    logger.info(f"\nStale enrichments (>7 days old, showing top 10): {stale_enrichments[:10]}")
    
    # Console report
    print("\n" + "=" * 60)
    print("ENRICHMENT COVERAGE REPORT")
    print("=" * 60)
    print(f"Total MCPs in registry: {total_mcps}")
    print(f"\nPer-Signal Coverage:")
    for sig, data in sorted(signal_coverage.items()):
        bar_len = min(int(data['pct'] / 2), 50)
        bar = '#' * bar_len + '-' * (50 - bar_len)
        print(f"  {sig:25s} [{bar}] {data['enriched']:4d} ({data['pct']:5.2f}%)")
    
    print(f"\nGap Signals (<10% coverage): {len(gap_signals)}")
    for sig in gap_signals[:5]:
        print(f"  - {sig}")
    
    print(f"\nZero-Coverage MCPs (showing top 10 of {len(zero_score_mcps)}):")
    for mcp in zero_score_mcps[:10]:
        print(f"  - {mcp}")
    
    print(f"\nStale Enrichments (>7 days, showing top 10 of {len(stale_enrichments)}):")
    for mcp in stale_enrichments[:10]:
        print(f"  - {mcp}")
    
    result = {
        'signal_coverage': signal_coverage,
        'gap_signals': gap_signals,
        'zero_score_mcps': zero_score_mcps,
        'stale_enrichments': stale_enrichments,
    }
    
    logger.info("\nDiagnostic completed successfully")
    return result


if __name__ == '__main__':
    print("Running enrichment coverage diagnostic self-test...")
    try:
        result = run_enrichment_coverage_diagnostic()
        
        # Assertions per acceptance criteria
        assert 'gap_signals' in result, "Missing 'gap_signals' in result"
        assert 'signal_coverage' in result, "Missing 'signal_coverage' in result"
        assert result['signal_coverage'], "signal_coverage must not be empty"
        assert 'zero_score_mcps' in result, "Missing 'zero_score_mcps' in result"
        assert 'stale_enrichments' in result, "Missing 'stale_enrichments' in result"
        
        print("\n" + "=" * 60)
        print("PASS: Self-test passed")
        print(f"  - Signal coverage computed for {len(result['signal_coverage'])} signals")
        print(f"  - Gap signals identified: {len(result['gap_signals'])}")
        print(f"  - Zero-score MCPs found: {len(result['zero_score_mcps'])}")
        print(f"  - Stale enrichments found: {len(result['stale_enrichments'])}")
        print("=" * 60)
        sys.exit(0)
    except Exception as e:
        print(f"\nFAIL: Self-test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
