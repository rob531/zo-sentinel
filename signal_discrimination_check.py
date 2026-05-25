import requests
import json
import time

def query_signal_scores():
    url = "http://127.0.0.1:8772/query"
    payload = {
        "sql": """
            SELECT 
                signal_name,
                score,
                COUNT(*) as count,
                COUNT(DISTINCT server_id) as fingerprint_count
            FROM mcp_signal_scores
            WHERE signal_name IN ('permission_scope', 'temporal_stability', 'tool_description_safety')
            GROUP BY signal_name, score
            ORDER BY signal_name, score
        """
    }
    response = requests.post(url, json=payload)
    response.raise_for_status()
    return response.json()

def count_distinct_scores():
    url = "http://127.0.0.1:8772/query"
    payload = {
        "sql": """
            SELECT 
                signal_name,
                COUNT(DISTINCT score) as distinct_score_count,
                COUNT(*) as total_records,
                COUNT(DISTINCT server_id) as unique_fingerprints
            FROM mcp_signal_scores
            WHERE signal_name IN ('permission_scope', 'temporal_stability', 'tool_description_safety')
            GROUP BY signal_name
            ORDER BY signal_name
        """
    }
    response = requests.post(url, json=payload)
    response.raise_for_status()
    return response.json()

def log_diagnostic(signal_name, distinct_count, total_records, fingerprints, status):
    url = "http://127.0.0.1:8772/write"
    payload = {
        "table": "service_health",
        "rows": {
            "service": "signal_discrimination_check",
            "last_heartbeat": int(time.time()),
            "meta": json.dumps({
                "signal": signal_name,
                "distinct_scores": distinct_count,
                "total_records": total_records,
                "fingerprints": fingerprints,
                "status": status,
                "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S")
            })
        }
    }
    response = requests.post(url, json=payload)
    response.raise_for_status()
    return response.json()

def main():
    print("=== Signal Discrimination Gap Diagnostic ===")
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    print("Querying mcp_signal_scores for target signals...")
    distinct_results = count_distinct_scores()
    
    print("\n--- Distinct Score Counts per Signal ---")
    if distinct_results.get('rows'):
        for row in distinct_results['rows']:
            signal = row.get('signal_name', 'unknown')
            distinct = row.get('distinct_score_count', 0)
            total = row.get('total_records', 0)
            fps = row.get('unique_fingerprints', 0)
            
            if distinct <= 3:
                status = "WEAK - low variety detected"
                print(f"  {signal}: {distinct} distinct values across {fps} fingerprints (total: {total}) - {status}")
            else:
                status = "OK"
                print(f"  {signal}: {distinct} distinct values across {fps} fingerprints (total: {total}) - {status}")
            
            log_diagnostic(signal, distinct, total, fps, status)
    else:
        print("  No data found for target signals")
    
    print("\n--- Score Distribution Detail ---")
    detail_results = query_signal_scores()
    if detail_results.get('rows'):
        current_signal = None
        for row in detail_results['rows']:
            signal = row.get('signal_name', 'unknown')
            score = row.get('score', 'N/A')
            count = row.get('count', 0)
            fps = row.get('fingerprint_count', 0)
            
            if signal != current_signal:
                if current_signal:
                    print()
                current_signal = signal
                print(f"{signal}:")
            
            print(f"  score={score} -> {count} records, {fps} fingerprints")
    
    print("\n--- Diagnostic Summary ---")
    if distinct_results.get('rows'):
        weak_signals = [r for r in distinct_results['rows'] if r.get('distinct_score_count', 0) <= 3]
        if weak_signals:
            print(f"ALERT: {len(weak_signals)} signals with WEAK discrimination (≤3 distinct values)")
            for ws in weak_signals:
                print(f"  - {ws['signal_name']}: {ws['distinct_score_count']} values, {ws['unique_fingerprints']} fingerprints")
            print("\nRecommendation: Human review of enrichment logic needed")
        else:
            print("All target signals show adequate discrimination (>3 distinct values)")

if __name__ == '__main__':
    main()