#!/usr/bin/env python3
"""
enrichment_wiring_diagnostic.py
Diagnostic to verify enrichment pipeline wiring for weak signals:
  - permission_scope
  - temporal_stability
  - tool_description_safety

Confirms >=20 distinct score values per signal_type per enrichment contract.
Reports enricher versions (v3/v4) producing rows.
Pure diagnostic - NO writes to DB.
"""

import requests
import sys
from datetime import datetime

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_ENDPOINT = f"{WRITE_SERVICE_URL}/query"

SIGNAL_TYPES = ['permission_scope', 'temporal_stability', 'tool_description_safety']
MIN_DISTINCT_SCORES = 20


def query_service(sql: str) -> dict:
    """Execute SELECT query via write_service HTTP API."""
    try:
        response = requests.post(
            QUERY_ENDPOINT,
            json={"sql": sql},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError as e:
        print(f"ERROR: Could not connect to write_service at {WRITE_SERVICE_URL}")
        print(f"Detail: {e}")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: HTTP error from write_service: {e}")
        print(f"Response: {e.response.text if e.response else 'N/A'}")
        sys.exit(1)


def check_signal_type_wiring(signal_type: str) -> dict:
    """Check wiring for a specific signal type."""
    
    # Check distinct score count per enricher version
    distinct_sql = f"""
    SELECT 
        COALESCE(enricher_version, 'unknown') as enricher_version,
        COUNT(DISTINCT score) as distinct_score_count,
        COUNT(*) as total_rows,
        MIN(scored_at) as first_enrichment,
        MAX(scored_at) as last_enrichment
    FROM mcp_signal_enrichments
    WHERE signal_type = '{signal_type}'
    GROUP BY enricher_version
    ORDER BY enricher_version
    """
    
    result = query_service(distinct_sql)
    rows = result.get('rows', [])
    
    # Check overall distinct scores for this signal type
    overall_sql = f"""
    SELECT 
        COUNT(DISTINCT score) as total_distinct_scores,
        COUNT(*) as total_enrichment_rows
    FROM mcp_signal_enrichments
    WHERE signal_type = '{signal_type}'
    """
    
    overall_result = query_service(overall_sql)
    overall = overall_result.get('rows', [{}])[0] if overall_result.get('rows') else {}
    
    return {
        'by_version': rows,
        'overall': overall
    }


def main():
    print("=" * 70)
    print("ENRICHMENT WIRING DIAGNOSTIC")
    print("=" * 70)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Write Service: {WRITE_SERVICE_URL}")
    print(f"Signal Types: {', '.join(SIGNAL_TYPES)}")
    print(f"Min Distinct Scores Required: {MIN_DISTINCT_SCORES}")
    print("=" * 70)
    
    all_passed = True
    summary = []
    
    for signal_type in SIGNAL_TYPES:
        print(f"\n{'=' * 60}")
        print(f"CHECKING: {signal_type}")
        print("=" * 60)
        
        try:
            wiring = check_signal_type_wiring(signal_type)
            by_version = wiring.get('by_version', [])
            overall = wiring.get('overall', {})
            
            if not by_version:
                print(f"  STATUS: NO ENRICHMENTS FOUND")
                print(f"  ACTION: Wire v3/v4 enricher for '{signal_type}'")
                all_passed = False
                summary.append({
                    'signal_type': signal_type,
                    'status': 'MISSING',
                    'versions': [],
                    'distinct_scores': 0
                })
                continue
            
            print(f"\n  Overall Statistics:")
            print(f"    Total distinct scores: {overall.get('total_distinct_scores', 0)}")
            print(f"    Total enrichment rows: {overall.get('total_enrichment_rows', 0)}")
            
            print(f"\n  By Enricher Version:")
            version_passed = True
            
            for version_row in by_version:
                version = version_row.get('enricher_version', 'unknown')
                distinct = version_row.get('distinct_score_count', 0)
                total = version_row.get('total_rows', 0)
                first = version_row.get('first_enrichment', 'N/A')
                last = version_row.get('last_enrichment', 'N/A')
                
                status = "PASS" if distinct >= MIN_DISTINCT_SCORES else "FAIL"
                if distinct < MIN_DISTINCT_SCORES:
                    version_passed = False
                
                print(f"\n    Version {version}:")
                print(f"      Distinct scores: {distinct} ({status})")
                print(f"      Total rows: {total}")
                print(f"      Date range: {first} to {last}")
                
                if distinct < MIN_DISTINCT_SCORES:
                    print(f"      WARNING: < {MIN_DISTINCT_SCORES} distinct scores!")
            
            if version_passed:
                print(f"\n  STATUS: PASS - All versions have >= {MIN_DISTINCT_SCORES} distinct scores")
                summary.append({
                    'signal_type': signal_type,
                    'status': 'PASS',
                    'versions': [v.get('enricher_version') for v in by_version],
                    'distinct_scores': overall.get('total_distinct_scores', 0)
                })
            else:
                print(f"\n  STATUS: FAIL - Some versions below {MIN_DISTINCT_SCORES} distinct scores")
                all_passed = False
                summary.append({
                    'signal_type': signal_type,
                    'status': 'FAIL',
                    'versions': [v.get('enricher_version') for v in by_version],
                    'distinct_scores': overall.get('total_distinct_scores', 0)
                })
                
        except Exception as e:
            print(f"  ERROR: {e}")
            all_passed = False
            summary.append({
                'signal_type': signal_type,
                'status': 'ERROR',
                'error': str(e)
            })
    
    # Final Summary Report
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    
    for item in summary:
        sig = item['signal_type']
        status = item['status']
        versions = item.get('versions', [])
        distinct = item.get('distinct_scores', 0)
        
        if status == 'PASS':
            print(f"  [PASS] {sig}: {len(versions)} version(s), {distinct} distinct scores")
            print(f"         Versions: {', '.join(str(v) for v in versions)}")
        elif status == 'FAIL':
            print(f"  [FAIL] {sig}: Below {MIN_DISTINCT_SCORES} distinct scores")
            print(f"         Versions: {', '.join(str(v) for v in versions)}")
        elif status == 'MISSING':
            print(f"  [MISSING] {sig}: No enrichments wired")
        else:
            print(f"  [ERROR] {sig}: {item.get('error', 'Unknown error')}")
    
    print("\n" + "=" * 70)
    
    if all_passed:
        print("RESULT: ALL ENRICHMENT WIRING CHECKS PASSED")
        print("No rebuild directive required.")
        return 0
    else:
        print("RESULT: WIRING GAPS DETECTED")
        print("Rebuild directive: Re-wire enrichers for failed signal types.")
        return 1


if __name__ == '__main__':
    sys.exit(main())