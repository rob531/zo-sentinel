#!/usr/bin/env python3
"""
diagnose_signal_discrimination_gap.py
Diagnostic module to analyze why three signals show only 3 distinct values.
"""

import requests
import json
from collections import defaultdict

WRITE_SERVICE = "http://127.0.0.1:8772"
PORT = 8785

def query(sql):
    """Execute a SELECT query via write_service."""
    response = requests.post(
        f"{WRITE_SERVICE}/query",
        json={"sql": sql},
        headers={"Content-Type": "application/json"}
    )
    result = response.json()
    return result.get('rows', []), result.get('count', 0)

def execute(sql):
    """Execute DDL/DML via write_service."""
    response = requests.post(
        f"{WRITE_SERVICE}/execute",
        json={"sql": sql},
        headers={"Content-Type": "application/json"}
    )
    return response.json()

def analyze_evidence_metadata(evidence_str):
    """Extract metadata keys from evidence JSON string."""
    if not evidence_str:
        return set()
    try:
        evidence = json.loads(evidence_str)
        if isinstance(evidence, dict):
            return set(evidence.keys())
        return set()
    except (json.JSONDecodeError, TypeError):
        return set()

def run_diagnosis():
    """Run the signal discrimination gap diagnosis."""
    
    print("=" * 80)
    print("ZO-SENTINEL: Signal Discrimination Gap Diagnosis")
    print("=" * 80)
    
    # Step 1: Get distinct value counts per signal_name
    distinct_query = """
    SELECT 
        signal_name,
        COUNT(DISTINCT score) as distinct_scores,
        COUNT(*) as total_records
    FROM mcp_signal_scores
    GROUP BY signal_name
    ORDER BY distinct_scores ASC
    """
    
    signal_counts, _ = query(distinct_query)
    
    print("\n[1] SIGNAL VALUE DISTRIBUTION")
    print("-" * 80)
    for row in signal_counts:
        print(f"  {row['signal_name']}: {row['distinct_scores']} distinct values " +
              f"({row['total_records']} records)")
    
    # Step 2: Categorize weak vs strong signals
    weak_signals = ['permission_scope', 'temporal_stability', 'tool_description_safety']
    strong_signals = ['supply_chain', 'community_signal']
    
    print("\n[2] SIGNAL CLASSIFICATION")
    print("-" * 80)
    print(f"  WEAK SIGNALS (3 distinct values): {weak_signals}")
    print(f"  STRONG SIGNALS (>30 distinct values): {strong_signals}")
    
    # Step 3: Analyze evidence metadata columns for each signal
    print("\n[3] ANALYZING EVIDENCE METADATA COLUMNS")
    print("-" * 80)
    
    all_signals = weak_signals + strong_signals
    signal_metadata = {}
    
    for signal in all_signals:
        samples_query = f"""
        SELECT evidence
        FROM mcp_signal_scores
        WHERE signal_name = '{signal}'
        LIMIT 50
        """
        samples, _ = query(samples_query)
        
        all_keys = set()
        for sample in samples:
            keys = analyze_evidence_metadata(sample.get('evidence'))
            all_keys.update(keys)
        
        signal_metadata[signal] = sorted(all_keys)
        print(f"\n  {signal.upper()}")
        print(f"    Metadata columns found: {signal_metadata[signal]}")
    
    # Step 4: Compute coverage analysis
    print("\n[4] METADATA COVERAGE ANALYSIS")
    print("-" * 80)
    
    weak_metadata = set()
    for s in weak_signals:
        weak_metadata.update(signal_metadata.get(s, []))
    
    strong_metadata = set()
    for s in strong_signals:
        strong_metadata.update(signal_metadata.get(s, []))
    
    print(f"  Weak signals metadata union: {sorted(weak_metadata)}")
    print(f"  Strong signals metadata union: {sorted(strong_metadata)}")
    
    # Find what's in strong but not in weak
    strong_only = strong_metadata - weak_metadata
    print(f"\n  METADATA IN STRONG BUT NOT WEAK: {sorted(strong_only)}")
    
    # Step 5: Analyze metadata usage frequency
    print("\n[5] METADATA USAGE FREQUENCY IN EVIDENCE")
    print("-" * 80)
    
    all_signals_list = [r['signal_name'] for r in signal_counts]
    metadata_frequency = defaultdict(lambda: defaultdict(int))
    
    for signal in all_signals_list:
        count_query = f"SELECT COUNT(*) as cnt FROM mcp_signal_scores WHERE signal_name = '{signal}'"
        rows, _ = query(count_query)
        total = rows[0]['cnt'] if rows else 0
        
        for sample_query in [f"""
            SELECT evidence
            FROM mcp_signal_scores
            WHERE signal_name = '{signal}'
            LIMIT 100
        """]:
            samples, _ = query(sample_query)
            for sample in samples:
                keys = analyze_evidence_metadata(sample.get('evidence'))
                for key in keys:
                    metadata_frequency[signal][key] += 1
    
    # Step 6: Get all available metadata columns from registry
    print("\n[6] AVAILABLE METADATA IN mcp_server_registry")
    print("-" * 80)
    
    registry_cols_query = """
    SELECT * FROM mcp_server_registry LIMIT 1
    """
    cols, _ = query(registry_cols_query)
    registry_columns = list(cols[0].keys()) if cols else []
    print(f"  Registry columns: {registry_columns}")
    
    # Step 7: Build recommendations
    print("\n[7] RECOMMENDATIONS")
    print("-" * 80)
    
    # Missing metadata fields that strong signals use but weak signals don't
    missing_for_weak = strong_only
    
    # Count servers with each metadata field populated
    field_population = {}
    for col in registry_columns:
        pop_query = f"""
        SELECT COUNT(*) as cnt FROM mcp_server_registry 
        WHERE {col} IS NOT NULL AND {col} != '' AND {col} != 'null'
        """
        rows, _ = query(pop_query)
        field_population[col] = rows[0]['cnt'] if rows else 0
    
    print("\n  Recommended metadata fields to add to weak signal computations:")
    recommendations = []
    for field in sorted(missing_for_weak):
        pop = field_population.get(field, 0)
        recommendations.append({
            'field': field,
            'population': pop,
            'reason': 'Used by strong signals but missing from weak signals'
        })
        print(f"    - {field}: populated in {pop} servers")
    
    # Check if there are other useful registry fields not currently used
    print("\n  Registry fields with high population (potential enrichment):")
    for col, pop in sorted(field_population.items(), key=lambda x: -x[1]):
        if col not in strong_metadata and col not in ['server_id']:
            if pop > 0:
                print(f"    - {col}: {pop} servers have data")
    
    # Step 8: Compute per-signal metadata breakdown
    print("\n[8] PER-SIGNAL METADATA BREAKDOWN")
    print("-" * 80)
    
    signal_analysis = []
    for signal in all_signals:
        info = {
            'signal': signal,
            'signal_type': 'weak' if signal in weak_signals else 'strong',
            'metadata_columns': signal_metadata.get(signal, []),
            'distinct_count': next((r['distinct_scores'] for r in signal_counts if r['signal_name'] == signal), 0)
        }
        signal_analysis.append(info)
        print(f"\n  {signal.upper()} ({info['signal_type']})")
        print(f"    Distinct values: {info['distinct_count']}")
        print(f"    Metadata columns: {info['metadata_columns']}")
    
    # Step 9: Store results in diagnostic table
    print("\n[9] PERSISTING RESULTS")
    print("-" * 80)
    
    # Create diagnostic results table if not exists
    execute("""
    CREATE TABLE IF NOT EXISTS signal_diagnosis (
        diagnosis_id INTEGER,
        signal_name VARCHAR,
        signal_type VARCHAR,
        distinct_value_count INTEGER,
        metadata_columns JSON,
        is_recommended_enrichment BOOLEAN,
        diagnosis_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Clear previous results
    execute("DELETE FROM signal_diagnosis")
    
    # Insert new results
    rec_fields = set(r['field'] for r in recommendations)
    for info in signal_analysis:
        is_rec = len(set(info['metadata_columns']) & rec_fields) > 0 if rec_fields else False
        execute(f"""
        INSERT INTO signal_diagnosis 
        (signal_name, signal_type, distinct_value_count, metadata_columns, is_recommended_enrichment)
        VALUES (
            '{info['signal']}',
            '{info['signal_type']}',
            {info['distinct_count']},
            '{json.dumps(info['metadata_columns'])}',
            {is_rec}
        )
        """)
    
    # Insert recommendation rows
    execute("""
    CREATE TABLE IF NOT EXISTS diagnosis_recommendations (
        field_name VARCHAR,
        reason VARCHAR,
        population_count INTEGER,
        diagnosis_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    execute("DELETE FROM diagnosis_recommendations")
    
    for rec in recommendations:
        execute(f"""
        INSERT INTO diagnosis_recommendations (field_name, reason, population_count)
        VALUES ('{rec['field']}', '{rec['reason']}', {rec['population']})
        """)
    
    # Step 10: Output final summary
    print("\n" + "=" * 80)
    print("DIAGNOSIS SUMMARY")
    print("=" * 80)
    
    summary = {
        'weak_signals': weak_signals,
        'weak_signal_distinct_counts': {s: next((r['distinct_scores'] for r in signal_counts if r['signal_name'] == s), 0) for s in weak_signals},
        'strong_signal_distinct_counts': {s: next((r['distinct_scores'] for r in signal_counts if r['signal_name'] == s), 0) for s in strong_signals},
        'weak_signal_metadata': sorted(weak_metadata),
        'strong_signal_metadata': sorted(strong_metadata),
        'metadata_missing_in_weak': sorted(strong_only),
        'recommended_enrichment_fields': [r['field'] for r in recommendations],
        'registry_fields_available': registry_columns,
        'field_population': field_population
    }
    
    print(json.dumps(summary, indent=2))
    
    print("\n" + "=" * 80)
    print("ROOT CAUSE IDENTIFIED")
    print("=" * 80)
    print(f"""
  The weak signals (permission_scope, temporal_stability, tool_description_safety)
  only read {len(weak_metadata)} unique metadata columns from evidence.
  
  The strong signals (supply_chain, community_signal) read {len(strong_metadata)} 
  unique columns including: {sorted(strong_only)}
  
  RECOMMENDED ACTION:
  - Enrich evidence computation for weak signals to include: {sorted(strong_only)}
  - These fields have data in {max(field_population.get(f, 0) for f in strong_only)} servers
  - Each additional field increases discrimination granularity exponentially
""")
    
    return summary

if __name__ == '__main__':
    result = run_diagnosis()