#!/usr/bin/env python3
import json
import sys
import time
from datetime import datetime, timedelta
import requests
from typing import Dict, List, Optional, Tuple

def read_rung_quota() -> Dict[str, Dict]:
    """Read rung_quota.json best-effort, return empty dict if file is missing or invalid."""
    try:
        with open('/home/workspace/zo_sentinel_state/rung_quota.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def get_model_stats() -> Dict[str, Dict[str, int]]:
    """Query per-model call stats from mesh_memory endpoint."""
    try:
        query = """
        SELECT
            model,
            COUNT(*) as total_calls,
            SUM(CASE WHEN success THEN 1 ELSE 0 END) as success_calls
        FROM mesh_memory
        WHERE
            memory_type = 'escalation_call'
            AND timestamp >= NOW() - INTERVAL '24 HOUR'
        GROUP BY model
        """
        response = requests.post(
            'http://127.0.0.1:8772/query',
            json={'sql': query},
            timeout=5
        )
        response.raise_for_status()
        return {row['model']: {'total_calls': row['total_calls'], 'success_calls': row['success_calls']}
                for row in response.json()}
    except (requests.RequestException, KeyError):
        return {}

def format_percent_bar(remaining: int, limit: int) -> str:
    """Format a percent bar with # characters."""
    if limit == 0:
        return "[----------] 0%"
    percent = min(100, int((remaining / limit) * 100))
    filled = int(percent / 10)
    return f"[{'#' * filled}{'-' * (10 - filled)}] {percent}%"

def is_parked(park_until: Optional[int]) -> bool:
    """Check if model is parked (park_until is a future epoch)."""
    if park_until is None:
        return False
    return park_until > int(time.time())

def main():
    json_output = '--json' in sys.argv
    rung_quota = read_rung_quota()
    model_stats = get_model_stats()

    if json_output:
        output = []
        for model_id, data in rung_quota.items():
            parked = is_parked(data.get('park_until'))
            remaining = data.get('remaining', 0)
            limit = data.get('limit', 0)
            stats = model_stats.get(model_id, {'total_calls': 0, 'success_calls': 0})

            output.append({
                'model_id': model_id,
                'remaining': remaining,
                'limit': limit,
                'percent': format_percent_bar(remaining, limit),
                'parked': parked,
                'total_calls': stats['total_calls'],
                'success_calls': stats['success_calls']
            })

        print(json.dumps(output, indent=2))
    else:
        print("Model ID\tRemaining/Limit\tFuel Gauge\tStatus\tTotal Calls\tSuccess Calls")
        print("--------\t------------\t----------\t------\t-----------\t-------------")

        for model_id, data in rung_quota.items():
            parked = is_parked(data.get('park_until'))
            remaining = data.get('remaining', 0)
            limit = data.get('limit', 0)
            stats = model_stats.get(model_id, {'total_calls': 0, 'success_calls': 0})

            status = "PARKED" if parked else ""
            print(f"{model_id}\t{remaining}/{limit}\t{format_percent_bar(remaining, limit)}\t{status}\t{stats['total_calls']}\t{stats['success_calls']}")

if __name__ == '__main__':
    main()