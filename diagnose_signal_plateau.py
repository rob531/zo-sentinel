import json
import httpx
from collections import Counter
from typing import Dict, List, Any
import math

WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"


def query_service(sql: str) -> List[Dict[str, Any]]:
    """Query write_service for read-only analysis."""
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(WRITE_SERVICE_URL, json={"sql": sql})
            response.raise_for_status()
            data = response.json()
            return data.get("rows", [])
    except Exception as e:
        print(f"Query error: {e}")
        return []


def compute_entropy(score_counts: Dict[float, int]) -> float:
    """Compute Shannon entropy of score distribution."""
    total = sum(score_counts.values())
    if total == 0:
        return 0.0
    entropy = 0.0
    for count in score_counts.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)
    return round(entropy, 4)


def get_max_entropy(num_distinct_values: int) -> float:
    """Maximum possible entropy for N distinct values."""
    if num_distinct_values <= 1:
        return 0.0
    return round(math.log2(num_distinct_values), 4)


def analyze_signal_plateau(signal_name: str) -> Dict[str, Any]:
    """Analyze score distribution for a single signal."""
    sql = f"""
        SELECT score, COUNT(*) as count
        FROM mcp_signal_scores
        WHERE signal_name = '{signal_name}'
        GROUP BY score
        ORDER BY score
    """
    rows = query_service(sql)
    
    if not rows:
        return {
            "signal_name": signal_name,
            "status": "no_data",
            "total_records": 0,
            "distinct_scores": 0,
            "entropy": 0.0,
            "max_entropy": 0.0,
            "entropy_ratio": 0.0,
            "score_distribution": {},
            "plateau_indicator": True
        }
    
    score_counts = {}
    for row in rows:
        score = row.get("score", 0)
        count = row.get("count", 0)
        score_counts[score] = count
    
    total_records = sum(score_counts.values())
    distinct_scores = len(score_counts)
    entropy = compute_entropy(score_counts)
    max_entropy = get_max_entropy(distinct_scores)
    entropy_ratio = round(entropy / max_entropy, 4) if max_entropy > 0 else 0.0
    
    plateau_threshold = 0.7
    plateau_indicator = entropy_ratio < plateau_threshold or distinct_scores <= 4
    
    return {
        "signal_name": signal_name,
        "status": "analyzed",
        "total_records": total_records,
        "distinct_scores": distinct_scores,
        "entropy": entropy,
        "max_entropy": max_entropy,
        "entropy_ratio": entropy_ratio,
        "score_distribution": score_counts,
        "plateau_indicator": plateau_indicator,
        "score_range": {
            "min": min(score_counts.keys()) if score_counts else None,
            "max": max(score_counts.keys()) if score_counts else None
        }
    }


def get_metadata_field_coverage() -> Dict[str, int]:
    """Check which metadata tables have data for scored servers."""
    coverage = {}
    
    tables_and_columns = {
        "mcp_ecosystems_metadata": "server_id",
        "mcp_fingerprints": "server_id",
        "mcp_attestations": "server_id",
        "mcp_registry_facts": "server_id",
        "mcp_threat_associations": "server_id"
    }
    
    for table, col in tables_and_columns.items():
        sql = f"SELECT COUNT(DISTINCT {col}) as cnt FROM {table}"
        rows = query_service(sql)
        coverage[table] = rows[0].get("cnt", 0) if rows else 0
    
    return coverage


def get_signal_metadata_correlation() -> Dict[str, Dict[str, int]]:
    """Analyze correlation between signals and metadata tables."""
    signals = [
        "permission_scope",
        "temporal_stability", 
        "tool_description_safety",
        "authoritative_trust",
        "community_reception",
        "ecosystem_placement"
    ]
    
    correlation = {}
    
    for signal in signals:
        signal_sql = f"""
            SELECT COUNT(DISTINCT s.server_id) as scored
            FROM mcp_signal_scores s
            WHERE s.signal_name = '{signal}'
        """
        signal_rows = query_service(signal_sql)
        scored_count = signal_rows[0].get("scored", 0) if signal_rows else 0
        
        correlations = {}
        for table in ["mcp_ecosystems_metadata", "mcp_fingerprints", "mcp_attestations"]:
            join_sql = f"""
                SELECT COUNT(DISTINCT s.server_id) as with_metadata
                FROM mcp_signal_scores s
                INNER JOIN {table} m ON s.server_id = m.server_id
                WHERE s.signal_name = '{signal}'
            """
            join_rows = query_service(join_sql)
            with_meta = join_rows[0].get("with_metadata", 0) if join_rows else 0
            correlations[table] = with_meta
        
        correlation[signal] = {
            "scored_total": scored_count,
            "with_ecosystem_metadata": correlations.get("mcp_ecosystems_metadata", 0),
            "with_fingerprints": correlations.get("mcp_fingerprints", 0),
            "with_attestations": correlations.get("mcp_attestations", 0)
        }
    
    return correlation


def diagnose_weak_signal_discrimination() -> Dict[str, Any]:
    """Main diagnostic function for signal plateau analysis."""
    
    signals = [
        "permission_scope",
        "temporal_stability",
        "tool_description_safety",
        "authoritative_trust",
        "community_reception",
        "ecosystem_placement"
    ]
    
    signal_analyses = {}
    plateau_signals = []
    
    for signal in signals:
        analysis = analyze_signal_plateau(signal)
        signal_analyses[signal] = analysis
        if analysis.get("plateau_indicator"):
            plateau_signals.append(signal)
    
    metadata_coverage = get_metadata_field_coverage()
    correlation = get_signal_metadata_correlation()
    
    scored_servers_sql = "SELECT COUNT(DISTINCT server_id) as cnt FROM mcp_signal_scores"
    scored_servers_rows = query_service(scored_servers_sql)
    total_scored = scored_servers_rows[0].get("cnt", 0) if scored_servers_rows else 0
    
    registry_total_sql = "SELECT COUNT(*) as cnt FROM mcp_server_registry"
    registry_rows = query_service(registry_total_sql)
    total_registry = registry_rows[0].get("cnt", 0) if registry_rows else 0
    
    report = {
        "diagnostic_timestamp": None,
        "summary": {
            "total_registry_servers": total_registry,
            "total_scored_servers": total_scored,
            "scoring_coverage_percent": round((total_scored / total_registry * 100), 2) if total_registry > 0 else 0,
            "signals_in_plateau": len(plateau_signals),
            "plateau_signals": plateau_signals,
            "critical_signals": [s for s in plateau_signals if signal_analyses.get(s, {}).get("distinct_scores", 0) <= 4]
        },
        "signal_analyses": signal_analyses,
        "metadata_coverage": metadata_coverage,
        "signal_metadata_correlation": correlation,
        "recommendations": []
    }
    
    ecosystem_count = metadata_coverage.get("mcp_ecosystems_metadata", 0)
    if ecosystem_count < total_scored * 0.5:
        report["recommendations"].append({
            "priority": "HIGH",
            "signal_affected": "ecosystem_placement",
            "issue": "Low ecosystem_metadata coverage",
            "recommendation": "Enrich mcp_ecosystems_metadata table - only {ecosystem_count} servers have metadata out of {total_scored} scored"
        })
    
    fingerprint_count = metadata_coverage.get("mcp_fingerprints", 0)
    if fingerprint_count < total_scored * 0.3:
        report["recommendations"].append({
            "priority": "HIGH",
            "signal_affected": "tool_description_safety",
            "issue": "Low fingerprint coverage",
            "recommendation": "Generate fingerprints for scored servers - only {fingerprint_count} servers have fingerprints"
        })
    
    for signal in plateau_signals:
        analysis = signal_analyses[signal]
        if analysis.get("distinct_scores", 0) <= 4:
            missing_enrichment = []
            corr = correlation.get(signal, {})
            if corr.get("with_ecosystem_metadata", 0) < total_scored * 0.3:
                missing_enrichment.append("ecosystem_metadata")
            if corr.get("with_fingerprints", 0) < total_scored * 0.3:
                missing_enrichment.append("fingerprints")
            if corr.get("with_attestations", 0) < total_scored * 0.3:
                missing_enrichment.append("attestations")
            
            if missing_enrichment:
                report["recommendations"].append({
                    "priority": "MEDIUM",
                    "signal_affected": signal,
                    "issue": f"Score plateau ({analysis.get('distinct_scores')} values) - low entropy ({analysis.get('entropy_ratio')})",
                    "recommendation": f"Enrich {', '.join(missing_enrichment)} for better discrimination"
                })
    
    return report


def main():
    from datetime import datetime
    report = diagnose_weak_signal_discrimination()
    report["diagnostic_timestamp"] = datetime.utcnow().isoformat() + "Z"
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()