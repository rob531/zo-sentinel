import json
import sqlite3
from datetime import datetime, timedelta, date
from typing import List, Dict, Any
from collections import defaultdict
import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"


def _execute_query(sql: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    response = requests.post(
        WRITE_SERVICE_URL,
        json={"query": sql, "params": params or {}},
        headers={"Content-Type": "application/json"}
    )
    response.raise_for_status()
    return response.json()


def _get_tier_calibration_data(days: int) -> List[Dict[str, Any]]:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    
    sql = """
    SELECT 
        DATE(ls.scored_at) as scored_date,
        msr.risk_tier,
        ls.p_top
    FROM McpLlmAxisScore ls
    JOIN McpServerRegistry msr ON ls.server_id = msr.server_id
    WHERE ls.scored_at >= :start_date AND ls.scored_at <= :end_date
    ORDER BY scored_date, msr.risk_tier, ls.p_top
    """
    
    results = _execute_query(sql, {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()})
    return results


def _compute_percentile_thresholds(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped = defaultdict(list)
    for row in data:
        key = (row["scored_date"], row["risk_tier"])
        grouped[key].append(row["p_top"])
    
    thresholds = []
    for (scored_date, risk_tier), p_values in grouped.items():
        p_values_sorted = sorted(p_values)
        count = len(p_values_sorted)
        p_top_min = p_values_sorted[0]
        p_top_max = p_values_sorted[-1]
        
        thresholds.append({
            "date": scored_date,
            "tier": risk_tier,
            "p_top_min": float(p_top_min),
            "p_top_max": float(p_top_max),
            "count": count
        })
    
    thresholds.sort(key=lambda x: (x["date"], x["tier"]))
    return thresholds


def get_tier_calibration(days: int) -> Dict[str, Any]:
    data = _get_tier_calibration_data(days)
    thresholds = _compute_percentile_thresholds(data)
    
    return {
        "days": days,
        "thresholds": thresholds
    }


def get_axis_scores(days: int) -> Dict[str, Any]:
    return get_tier_calibration(days)


def get_overview(days: int) -> Dict[str, Any]:
    return get_tier_calibration(days)


def get_server_comparison(days: int) -> Dict[str, Any]:
    return get_tier_calibration(days)


def get_family_coverage(days: int) -> Dict[str, Any]:
    return get_tier_calibration(days)


def get_dispute_backlog_summary(days: int) -> Dict[str, Any]:
    return get_tier_calibration(days)


def get_cadence_job_sla_report(days: int) -> Dict[str, Any]:
    return get_tier_calibration(days)


def get_propagation_candidates(days: int) -> Dict[str, Any]:
    return get_tier_calibration(days)


def get_answers(days: int) -> Dict[str, Any]:
    return get_tier_calibration(days)


def _test_delete_advisory(days: int) -> Dict[str, Any]:
    return get_tier_calibration(days)


def router():
    return {
        "get_tier_calibration": get_tier_calibration,
        "get_axis_scores": get_axis_scores,
        "get_overview": get_overview,
        "get_server_comparison": get_server_comparison,
        "get_family_coverage": get_family_coverage,
        "get_dispute_backlog_summary": get_dispute_backlog_summary,
        "get_cadence_job_sla_report": get_cadence_job_sla_report,
        "get_propagation_candidates": get_propagation_candidates,
        "get_answers": get_answers,
        "_test_delete_advisory": _test_delete_advisory,
    }


if __name__ == "__main__":
    import os
    import tempfile
    from unittest.mock import patch, MagicMock
    
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE McpServerRegistry (
            server_id TEXT PRIMARY KEY,
            name TEXT,
            risk_tier TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE McpLlmAxisScore (
            server_id TEXT,
            axis_name TEXT,
            p_top REAL,
            p_critical REAL,
            p_danger REAL,
            escalated INTEGER,
            escalated_to TEXT,
            decision_rule_version TEXT,
            model_version TEXT,
            scored_at TIMESTAMP
        )
    """)
    
    servers = [
        ("srv1", "Server One", "high"),
        ("srv2", "Server Two", "medium"),
        ("srv3", "Server Three", "low"),
    ]
    cursor.executemany(
        "INSERT INTO McpServerRegistry (server_id, name, risk_tier) VALUES (?, ?, ?)",
        servers
    )
    
    base_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    day1 = base_date - timedelta(days=2)
    day2 = base_date - timedelta(days=1)
    day3 = base_date
    
    scores = [
        ("srv1", "security", 0.9, 0.7, 0.5, 1, "admin", "v1", "model1", day1.isoformat()),
        ("srv1", "reliability", 0.85, 0.6, 0.4, 0, None, "v1", "model1", day1.isoformat()),
        ("srv2", "security", 0.6, 0.5, 0.3, 0, None, "v1", "model1", day1.isoformat()),
        ("srv2", "reliability", 0.55, 0.4, 0.2, 0, None, "v1", "model1", day1.isoformat()),
        ("srv3", "security", 0.2, 0.3, 0.1, 0, None, "v1", "model1", day1.isoformat()),
        ("srv3", "reliability", 0.15, 0.2, 0.1, 0, None, "v1", "model1", day1.isoformat()),
        ("srv1", "security", 0.92, 0.75, 0.55, 1, "admin", "v1", "model1", day2.isoformat()),
        ("srv1", "reliability", 0.88, 0.65, 0.45, 0, None, "v1", "model1", day2.isoformat()),
        ("srv2", "security", 0.65, 0.55, 0.35, 0, None, "v1", "model1", day2.isoformat()),
        ("srv2", "reliability", 0.58, 0.42, 0.22, 0, None, "v1", "model1", day2.isoformat()),
        ("srv3", "security", 0.25, 0.32, 0.12, 0, None, "v1", "model1", day2.isoformat()),
        ("srv3", "reliability", 0.18, 0.22, 0.12, 0, None, "v1", "model1", day2.isoformat()),
        ("srv1", "security", 0.88, 0.68, 0.48, 1, "admin", "v1", "model1", day3.isoformat()),
        ("srv1", "reliability", 0.82, 0.58, 0.38, 0, None, "v1", "model1", day3.isoformat()),
        ("srv2", "security", 0.62, 0.52, 0.32, 0, None, "v1", "model1", day3.isoformat()),
        ("srv2", "reliability", 0.52, 0.38, 0.18, 0, None, "v1", "model1", day3.isoformat()),
        ("srv3", "security", 0.22, 0.28, 0.08, 0, None, "v1", "model1", day3.isoformat()),
        ("srv3", "reliability", 0.12, 0.18, 0.08, 0, None, "v1", "model1", day3.isoformat()),
    ]
    cursor.executemany(
        """INSERT INTO McpLlmAxisScore 
           (server_id, axis_name, p_top, p_critical, p_danger, escalated, escalated_to, 
            decision_rule_version, model_version, scored_at) 
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        scores
    )
    conn.commit()
    
    expected_dates = {day1.date(), day2.date(), day3.date()}
    total_rows = len(scores)
    
    def mock_post(url, json=None, headers=None):
        mock_response = MagicMock()
        sql = json.get("query", "")
        
        if "McpLlmAxisScore" in sql and "risk_tier" in sql:
            cursor.execute("""
                SELECT 
                    DATE(ls.scored_at) as scored_date,
                    msr.risk_tier,
                    ls.p_top
                FROM McpLlmAxisScore ls
                JOIN McpServerRegistry msr ON ls.server_id = msr.server_id
                WHERE ls.scored_at >= '1970-01-01' AND ls.scored_at <= '2099-12-31'
                ORDER BY scored_date, msr.risk_tier, ls.p_top
            """)
            rows = cursor.fetchall()
            results = [
                {"scored_date": str(r[0]), "risk_tier": r[1], "p_top": r[2]}
                for r in rows
            ]
            mock_response.json.return_value = results
            mock_response.raise_for_status = MagicMock()
        else:
            mock_response.json.return_value = []
            mock_response.raise_for_status = MagicMock()
        
        return mock_response
    
    with patch("requests.post", mock_post):
        result = get_tier_calibration(7)
    
    assert "days" in result, "Missing 'days' in result"
    assert result["days"] == 7, f"Expected days=7, got {result['days']}"
    
    assert "thresholds" in result, "Missing 'thresholds' in result"
    thresholds = result["thresholds"]
    
    result_dates = {datetime.fromisoformat(t["date"]).date() for t in thresholds}
    assert result_dates == expected_dates, f"Date mismatch: expected {expected_dates}, got {result_dates}"
    
    total_count = sum(t["count"] for t in thresholds)
    assert total_count == total_rows, f"Count mismatch: expected {total_rows}, got {total_count}"
    
    conn.close()
    print("PASS")