#!/usr/bin/env python3
"""
ZO-SENTINEL: Supply Chain Enrichment Integration Verification
Checks that supply_chain_enrichment and community_signal_enrichment are being called
and properly integrated into the system.
"""

import requests
import json
import sys
from datetime import datetime, timezone


WRITE_SERVICE = "http://127.0.0.1:8772/write"
INFERENCE_ROUTER = "http://127.0.0.1:8773/route"


def query_enrichments_table(signal_types: list) -> dict:
    """Query mcp_signal_enrichments table for specific signal types."""
    query = f"""
    SELECT 
        signal_type,
        COUNT(*) as count,
        MIN(timestamp) as first_seen,
        MAX(timestamp) as last_seen
    FROM mcp_signal_enrichments 
    WHERE signal_type IN ({','.join(["'" + st + "'" for st in signal_types])})
    GROUP BY signal_type
    """
    
    payload = {
        "table": "mcp_signal_enrichments",
        "query": query,
        "wait": True
    }
    
    try:
        response = requests.post(WRITE_SERVICE, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e), "raw_query": query}


def get_sample_enrichments(signal_type: str, limit: int = 5) -> list:
    """Get sample evidence blobs for a specific signal type."""
    query = f"""
    SELECT 
        signal_id,
        signal_type,
        target_server_id,
        evidence_blob,
        timestamp
    FROM mcp_signal_enrichments 
    WHERE signal_type = '{signal_type}'
    ORDER BY timestamp DESC
    LIMIT {limit}
    """
    
    payload = {
        "table": "mcp_signal_enrichments",
        "query": query,
        "wait": True
    }
    
    try:
        response = requests.post(WRITE_SERVICE, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return [{"error": str(e)}]


def check_audit_log_for_enrichment_calls() -> dict:
    """Check audit_log for enrichment function invocations."""
    query = """
    SELECT 
        action,
        COUNT(*) as count,
        MAX(timestamp) as last_called
    FROM audit_log 
    WHERE action LIKE '%enrichment%' 
       OR action LIKE '%supply_chain%'
       OR target_server_id IN (
           SELECT DISTINCT target_server_id 
           FROM mcp_signal_enrichments 
           WHERE signal_type IN ('supply_chain_enrichment', 'community_signal_enrichment')
       )
    GROUP BY action
    ORDER BY count DESC
    LIMIT 20
    """
    
    payload = {
        "table": "audit_log",
        "query": query,
        "wait": True
    }
    
    try:
        response = requests.post(WRITE_SERVICE, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e)}


def analyze_evidence_blob_structure(samples: list) -> dict:
    """Analyze the structure of evidence blobs."""
    structures = {}
    
    for sample in samples:
        if "error" in sample:
            continue
        
        signal_type = sample.get("signal_type", "unknown")
        evidence = sample.get("evidence_blob", {})
        
        if signal_type not in structures:
            structures[signal_type] = {
                "keys": list(evidence.keys()) if isinstance(evidence, dict) else "N/A",
                "sample_count": 0,
                "integrations_detected": []
            }
        
        structures[signal_type]["sample_count"] += 1
        
        # Detect what integrations are present
        if isinstance(evidence, dict):
            if "dependency_graph" in evidence or "dependencies" in evidence:
                structures[signal_type]["integrations_detected"].append("dependency_analysis")
            if "community_score" in evidence or "community_signals" in evidence:
                structures[signal_type]["integrations_detected"].append("community_signals")
            if "vulnerability_count" in evidence or "cves" in evidence:
                structures[signal_type]["integrations_detected"].append("vulnerability_data")
            if "supplier_risk" in evidence or "risk_score" in evidence:
                structures[signal_type]["integrations_detected"].append("risk_assessment")
    
    return structures


def check_signal_generation_table() -> dict:
    """Check if signals are being generated from enrichments."""
    query = """
    SELECT 
        signal_type,
        COUNT(*) as count,
        MAX(timestamp) as last_generated
    FROM mcp_signal 
    WHERE signal_type LIKE '%supply_chain%' 
       OR signal_type LIKE '%community%'
    GROUP BY signal_type
    """
    
    payload = {
        "table": "mcp_signal",
        "query": query,
        "wait": True
    }
    
    try:
        response = requests.post(WRITE_SERVICE, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e)}


def main():
    print("=" * 70)
    print("ZO-SENTINEL: Enrichment Integration Verification")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)
    
    signal_types = ['supply_chain_enrichment', 'community_signal_enrichment']
    
    # 1. Check enrichment table counts
    print("\n[1] ENRICHMENT TABLE COUNTS")
    print("-" * 40)
    counts = query_enrichments_table(signal_types)
    
    if "error" in counts:
        print(f"ERROR querying enrichments table: {counts['error']}")
    elif "values" in counts:
        for row in counts.get("values", []):
            print(f"  {row[0]}: {row[1]} records (first: {row[2]}, last: {row[3]})")
    else:
        print(f"  No enrichments found: {counts}")
    
    # 2. Get sample evidence blobs
    print("\n[2] SAMPLE EVIDENCE BLOB ANALYSIS")
    print("-" * 40)
    
    for st in signal_types:
        samples = get_sample_enrichments(st, limit=3)
        if samples and "error" not in samples[0]:
            blob_structures = analyze_evidence_blob_structure(samples)
            for signal_type, info in blob_structures.items():
                print(f"  {signal_type}:")
                print(f"    Keys: {info['keys']}")
                print(f"    Integrations: {info['integrations_detected']}")
        else:
            print(f"  {st}: No samples available")
    
    # 3. Check audit log for enrichment calls
    print("\n[3] AUDIT LOG - ENRICHMENT CALLS")
    print("-" * 40)
    audit_calls = check_audit_log_for_enrichment_calls()
    
    if "error" in audit_calls:
        print(f"  ERROR: {audit_calls['error']}")
    elif "values" in audit_calls:
        for row in audit_calls.get("values", [])[:10]:
            print(f"  {row[0]}: {row[1]} calls (last: {row[2]})")
    else:
        print("  No enrichment-related audit entries found")
    
    # 4. Check if signals are being generated from enrichments
    print("\n[4] SIGNAL GENERATION FROM ENRICHMENTS")
    print("-" * 40)
    signals = check_signal_generation_table()
    
    if "error" in signals:
        print(f"  ERROR: {signals['error']}")
    elif "values" in signals:
        for row in signals.get("values", []):
            print(f"  {row[0]}: {row[1]} signals (last: {row[2]})")
    else:
        print("  No supply_chain/community signals found")
    
    # 5. Integration status summary
    print("\n[5] INTEGRATION STATUS SUMMARY")
    print("-" * 40)
    
    supply_chain_count = 0
    community_count = 0
    
    if "values" in counts:
        for row in counts.get("values", []):
            if row[0] == 'supply_chain_enrichment':
                supply_chain_count = row[1]
            elif row[0] == 'community_signal_enrichment':
                community_count = row[1]
    
    if supply_chain_count > 0:
        print("  ✓ supply_chain_enrichment: INTEGRATED ({count} enrichments)".format(count=supply_chain_count))
    else:
        print("  ✗ supply_chain_enrichment: NOT INTEGRATED (0 enrichments)")
    
    if community_count > 0:
        print("  ✓ community_signal_enrichment: INTEGRATED ({count} enrichments)".format(count=community_count))
    else:
        print("  ✗ community_signal_enrichment: NOT INTEGRATED (0 enrichments)")
    
    # 6. Recommendations
    print("\n[6] RECOMMENDATIONS")
    print("-" * 40)
    
    if supply_chain_count == 0:
        print("  • supply_chain_enrichment.py may not be called in signal processing pipeline")
        print("  • Check: threat_intel_ingestor.py -> cycle() -> enrichment calls")
    
    if community_count == 0:
        print("  • community_signal_enrichment.py may not be called in signal processing pipeline")
        print("  • Check: signal ingestion flow includes community enrichment step")
    
    if supply_chain_count > 0 and community_count > 0:
        print("  ✓ Both enrichments are active and integrated")
        print("  • Monitor for enrichment quality and coverage metrics")
    
    print("\n" + "=" * 70)
    return 0 if (supply_chain_count > 0 or community_count > 0) else 1


if __name__ == '__main__':
    sys.exit(main())