#!/usr/bin/env python3
"""
Signal Enrichment Coverage Reporter

Utility that audits signal enrichment coverage and reports gaps per MCP.
Reads mcp_signal_enrichments and mcp_server_registry via write_service /query.
No DB writes. Stdlib only.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"

# The 8 signals required for a complete composite per PRODUCT_SPEC
REQUIRED_SIGNALS = [
    "evidence_density",
    "ecosystem_relevance",
    "attestation_quality",
    "fingerprint_diversity",
    "github_velocity",
    "directory_presence",
    "ecosystem_metadata",
    "submission_quality",
]


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """Query via write_service /query endpoint.
    
    Args:
        sql: SQL query string (user values as $1, $2 placeholders in DuckDB).
        params: List of parameter values.
        
    Returns:
        List of result rows as dicts.
    """
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(f"Query error: {result['error']}")
    return result if isinstance(result, list) else []


def get_enrichment_coverage(mcp_server_id: str) -> Dict[str, Any]:
    """Get enrichment coverage for a specific MCP server.
    
    Args:
        mcp_server_id: The server_id to query.
        
    Returns:
        dict with keys:
            - total_signals: int (count of distinct signal types present)
            - missing_signals: list[str] (signal types not present)
            - coverage_pct: float (0-100)
            - last_enriched: str (ISO 8601 or None)
    """
    # Get distinct signal types present for this server
    sql = """
        SELECT DISTINCT enrichment_type
        FROM mcp_signal_enrichments
        WHERE server_id = ?
    """
    rows = ws_query(sql, params=[mcp_server_id])
    present_signals = {r["enrichment_type"] for r in rows if r.get("enrichment_type")}
    
    # Get last enrichment timestamp
    sql_last = """
        SELECT enriched_at
        FROM mcp_signal_enrichments
        WHERE server_id = ?
        ORDER BY enriched_at DESC
        LIMIT 1
    """
    last_rows = ws_query(sql_last, params=[mcp_server_id])
    last_enriched: Optional[str] = None
    if last_rows and last_rows[0].get("enriched_at"):
        ts = last_rows[0]["enriched_at"]
        # Normalize to ISO 8601 string
        if isinstance(ts, str):
            last_enriched = ts
        elif hasattr(ts, "isoformat"):
            last_enriched = ts.isoformat()
        else:
            last_enriched = str(ts)
    
    # Compute missing signals
    missing_signals = sorted([s for s in REQUIRED_SIGNALS if s not in present_signals])
    total_signals = len(present_signals & set(REQUIRED_SIGNALS))
    coverage_pct = round((total_signals / len(REQUIRED_SIGNALS)) * 100, 2)
    
    return {
        "total_signals": total_signals,
        "missing_signals": missing_signals,
        "coverage_pct": coverage_pct,
        "last_enriched": last_enriched,
    }


def get_all_gaps() -> List[Dict[str, Any]]:
    """Get all MCPs with enrichment coverage < 100%.
    
    Returns:
        List of dicts sorted by coverage_pct ascending.
        Each entry: {mcp_server_id, mcp_name, coverage_pct, missing_signals}.
    """
    sql = """
        SELECT r.server_id, r.name AS mcp_name
        FROM mcp_server_registry r
        ORDER BY r.server_id
    """
    registry_rows = ws_query(sql)
    
    gaps: List[Dict[str, Any]] = []
    
    for row in registry_rows:
        server_id = row.get("server_id")
        mcp_name = row.get("mcp_name", "")
        
        # Get present signals for this server
        sql_signals = """
            SELECT DISTINCT enrichment_type
            FROM mcp_signal_enrichments
            WHERE server_id = ?
        """
        signal_rows = ws_query(sql_signals, params=[server_id])
        present_signals = {r["enrichment_type"] for r in signal_rows if r.get("enrichment_type")}
        
        missing_signals = sorted([s for s in REQUIRED_SIGNALS if s not in present_signals])
        
        if missing_signals:  # Only include if there are gaps
            total_signals = len(present_signals & set(REQUIRED_SIGNALS))
            coverage_pct = round((total_signals / len(REQUIRED_SIGNALS)) * 100, 2)
            
            gaps.append({
                "mcp_server_id": server_id,
                "mcp_name": mcp_name,
                "coverage_pct": coverage_pct,
                "missing_signals": missing_signals,
            })
    
    gaps.sort(key=lambda x: x["coverage_pct"])
    return gaps


def report_gaps(threshold: int = 8) -> str:
    """Format enrichment gaps as a readable text table.
    
    Args:
        threshold: Minimum signals required (default 8).
        
    Returns:
        Formatted string table of MCPs with coverage gaps.
    """
    all_gaps = get_all_gaps()
    
    if not all_gaps:
        return "No enrichment gaps found. All MCPs have complete signal coverage.\n"
    
    # Build table
    lines: List[str] = []
    lines.append("=" * 80)
    lines.append("SIGNAL ENRICHMENT COVERAGE GAPS")
    lines.append(f"Required signals per MCP: {threshold}")
    lines.append("=" * 80)
    lines.append("")
    lines.append(
        f"{'MCP Server ID':<40} {'MCP Name':<20} {'Coverage %':<12} {'Missing Signals'}"
    )
    lines.append("-" * 80)
    
    for gap in all_gaps:
        server_id = gap["mcp_server_id"] or ""
        mcp_name = gap["mcp_name"] or ""
        coverage = gap["coverage_pct"]
        missing = ", ".join(gap["missing_signals"]) if gap["missing_signals"] else "none"
        
        # Truncate long fields for display
        display_server = server_id[:38] + ".." if len(server_id) > 40 else server_id
        display_name = mcp_name[:18] + ".." if len(mcp_name) > 20 else mcp_name
        
        lines.append(
            f"{display_server:<40} {display_name:<20} {coverage:>6.2f}%     {missing}"
        )
    
    lines.append("")
    lines.append(f"Total MCPs with gaps: {len(all_gaps)}")
    lines.append("=" * 80)
    
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    from unittest.mock import patch, MagicMock
    
    print("Running self-test with mocked write_service...")
    
    # Mock responses for write_service /query endpoint
    def mock_post(url, json=None, timeout=None):
        mock_resp = MagicMock()
        sql = json.get("sql", "") if json else ""
        params = json.get("params", []) if json else []
        
        # Parse server_id from params if present
        server_id = params[0] if params else None
        
        if "enrichment_type" in sql.lower() and "distinct" in sql.lower():
            # Return mock signal types - simulate having some but not all
            if server_id and "test-" in str(server_id):
                # Test server has 5 of 8 signals
                mock_resp.json.return_value = [
                    {"enrichment_type": "evidence_density"},
                    {"enrichment_type": "ecosystem_relevance"},
                    {"enrichment_type": "attestation_quality"},
                    {"enrichment_type": "fingerprint_diversity"},
                    {"enrichment_type": "github_velocity"},
                ]
            else:
                mock_resp.json.return_value = []
        elif "enriched_at" in sql.lower():
            mock_resp.json.return_value = [
                {"enriched_at": "2026-06-17T10:30:00+00:00"}
            ]
        elif "mcp_server_registry" in sql.lower() and "left join" in sql.lower():
            mock_resp.json.return_value = []
        elif "mcp_server_registry" in sql.lower() and "server_id" in sql.lower():
            mock_resp.json.return_value = [
                {"server_id": "test-mcp-001", "mcp_name": "Test MCP Alpha"},
                {"server_id": "test-mcp-002", "mcp_name": "Test MCP Beta"},
            ]
        else:
            mock_resp.json.return_value = []
        
        mock_resp.raise_for_status = MagicMock()
        return mock_resp
    
    test_server_id = "test-mcp-001"
    
    with patch("requests.post", side_effect=mock_post):
        # Test 1: get_enrichment_coverage
        result = get_enrichment_coverage(test_server_id)
        
        assert "coverage_pct" in result, "Missing coverage_pct in result"
        assert isinstance(result["coverage_pct"], (int, float)), "coverage_pct must be numeric"
        assert 0 <= result["coverage_pct"] <= 100, f"coverage_pct must be 0-100, got {result['coverage_pct']}"
        assert "missing_signals" in result, "Missing missing_signals in result"
        assert isinstance(result["missing_signals"], list), "missing_signals must be a list"
        assert "total_signals" in result, "Missing total_signals in result"
        assert "last_enriched" in result, "Missing last_enriched in result"
        
        print(f"  get_enrichment_coverage: OK (coverage_pct={result['coverage_pct']}, "
              f"missing={len(result['missing_signals'])})")
        
        # Test 2: get_all_gaps
        gaps = get_all_gaps()
        assert isinstance(gaps, list), "get_all_gaps must return a list"
        print(f"  get_all_gaps: OK (found {len(gaps)} gaps)")
        
        # Test 3: report_gaps contains the test server_id
        report = report_gaps()
        assert isinstance(report, str), "report_gaps must return a string"
        assert test_server_id in report, f"report_gaps output must contain {test_server_id}"
        print(f"  report_gaps: OK (contains test server_id)")
        
        # Test 4: coverage_pct calculation is correct
        # With 5 of 8 signals: 5/8 * 100 = 62.5%
        assert result["coverage_pct"] == 62.5, f"Expected 62.5%, got {result['coverage_pct']}"
        print(f"  coverage_pct calculation: OK (62.5% for 5/8 signals)")
    
    print("\nPASS")
    sys.exit(0)
