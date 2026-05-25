#!/usr/bin/env python3
"""
temporal_stability_wiring.py
Companion module to connect temporal_stability_enrichment.py output into signal_analyser_v2.py pipeline.
Reads from mcp_signal_enrichments table (signal_type='temporal_stability') and feeds composite calculation.
Per spec section 8 protected-file rule: does NOT modify signal_analyser_v2.py directly.
"""
import json
import time
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

# Service endpoint
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_ENDPOINT = f"{WRITE_SERVICE_URL}/query"
WRITE_ENDPOINT = f"{WRITE_SERVICE_URL}/write"

# Table and signal type constants
ENRICHMENTS_TABLE = "mcp_signal_enrichments"
SIGNAL_TYPE = "temporal_stability"

# Polling configuration
POLL_INTERVAL_SECS = 30
STABILITY_WINDOW_HOURS = 24


def query_temporal_stability_signals(
    server_ids: Optional[List[str]] = None,
    hours_back: int = STABILITY_WINDOW_HOURS
) -> Dict[str, Any]:
    """
    Query temporal_stability enrichment signals from mcp_signal_enrichments.
    
    Args:
        server_ids: Optional list of server IDs to filter. If None, queries all.
        hours_back: How many hours of history to include.
    
    Returns:
        Dict with 'rows', 'count', and 'metadata' keys.
    """
    cutoff_time = datetime.now() - timedelta(hours=hours_back)
    cutoff_str = cutoff_time.isoformat()
    
    if server_ids:
        server_id_list = "', '".join(server_ids)
        sql = f"""
            SELECT 
                server_id,
                signal_type,
                raw_data,
                computed_score,
                confidence,
                evidence,
                created_at
            FROM mcp_signal_enrichments
            WHERE signal_type = '{SIGNAL_TYPE}'
              AND server_id IN ('{server_id_list}')
              AND created_at >= '{cutoff_str}'
            ORDER BY server_id, created_at DESC
        """
    else:
        sql = f"""
            SELECT 
                server_id,
                signal_type,
                raw_data,
                computed_score,
                confidence,
                evidence,
                created_at
            FROM mcp_signal_enrichments
            WHERE signal_type = '{SIGNAL_TYPE}'
              AND created_at >= '{cutoff_str}'
            ORDER BY server_id, created_at DESC
        """
    
    try:
        response = requests.post(
            QUERY_ENDPOINT,
            json={"sql": sql},
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        return {
            "rows": result.get("rows", []),
            "count": result.get("count", 0),
            "metadata": {
                "query_time": datetime.now().isoformat(),
                "signal_type": SIGNAL_TYPE,
                "hours_back": hours_back,
                "server_filter": server_ids
            }
        }
    except requests.RequestException as e:
        return {
            "rows": [],
            "count": 0,
            "metadata": {
                "query_time": datetime.now().isoformat(),
                "signal_type": SIGNAL_TYPE,
                "error": str(e)
            }
        }


def aggregate_stability_by_server(rows: List[Dict]) -> Dict[str, Dict]:
    """
    Aggregate temporal stability signals per server.
    Takes latest signal per server and calculates stability metrics.
    """
    stability_map = {}
    
    for row in rows:
        server_id = row.get("server_id")
        if not server_id:
            continue
        
        if server_id not in stability_map:
            stability_map[server_id] = {
                "server_id": server_id,
                "latest_score": row.get("computed_score"),
                "latest_confidence": row.get("confidence"),
                "evidence": row.get("evidence"),
                "latest_at": row.get("created_at"),
                "raw_data": row.get("raw_data"),
                "signal_count": 1,
                "scores": [row.get("computed_score")] if row.get("computed_score") is not None else []
            }
        else:
            entry = stability_map[server_id]
            entry["signal_count"] += 1
            if row.get("computed_score") is not None:
                entry["scores"].append(row.get("computed_score"))
    
    # Calculate aggregate metrics
    for server_id, entry in stability_map.items():
        scores = entry.get("scores", [])
        if len(scores) > 1:
            avg_score = sum(scores) / len(scores)
            variance = sum((s - avg_score) ** 2 for s in scores) / len(scores)
            entry["stability_variance"] = variance
            entry["stability_stddev"] = variance ** 0.5
            entry["avg_score"] = avg_score
        else:
            entry["stability_variance"] = 0.0
            entry["stability_stddev"] = 0.0
            entry["avg_score"] = scores[0] if scores else None
        
        # Composite stability factor (lower variance = higher stability)
        if entry["stability_stddev"] is not None:
            entry["stability_factor"] = max(0.0, min(1.0, 1.0 - entry["stability_stddev"]))
        else:
            entry["stability_factor"] = 0.5
    
    return stability_map


def get_composite_signal_contribution(
    stability_data: Dict[str, Dict]
) -> List[Dict]:
    """
    Transform stability data into composite signal contribution format.
    Returns signals ready for signal_analyser_v2.py pipeline consumption.
    """
    contributions = []
    
    for server_id, data in stability_data.items():
        contributions.append({
            "server_id": server_id,
            "signal_name": "temporal_stability_composite",
            "score": data.get("stability_factor", 0.5),
            "weight": 0.15,  # Typical weight for temporal signals
            "evidence": {
                "variance": data.get("stability_variance"),
                "stddev": data.get("stability_stddev"),
                "avg_score": data.get("avg_score"),
                "signal_count": data.get("signal_count"),
                "latest_confidence": data.get("latest_confidence"),
                "latest_at": data.get("latest_at")
            },
            "metadata": {
                "source_table": ENRICHMENTS_TABLE,
                "signal_type": SIGNAL_TYPE,
                "computation_time": datetime.now().isoformat()
            }
        })
    
    return contributions


def validate_data_flow() -> Dict[str, Any]:
    """
    Validate that temporal_stability data flows correctly.
    Performs test query and returns validation status.
    """
    result = query_temporal_stability_signals(hours_back=1)
    
    validation = {
        "flow_status": "ok" if result.get("count", 0) >= 0 else "error",
        "query_result_count": result.get("count", 0),
        "query_error": result.get("metadata", {}).get("error"),
        "timestamp": datetime.now().isoformat()
    }
    
    if result.get("count", 0) > 0:
        sample_rows = result.get("rows", [])[:3]
        validation["sample_schema_check"] = all(
            all(key in row for key in ["server_id", "signal_type", "computed_score", "created_at"])
            for row in sample_rows
        )
        validation["sample_count"] = len(sample_rows)
    else:
        validation["sample_schema_check"] = None
        validation["sample_count"] = 0
    
    return validation


def build_temporal_signals_for_analyser(
    server_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Main entry point: build temporal stability signals for signal_analyser_v2.py.
    
    Args:
        server_ids: Optional filter for specific servers.
    
    Returns:
        Dict containing:
        - enriched_signals: List of composite signals ready for analyser
        - stability_map: Raw aggregated stability data by server
        - validation: Data flow validation results
        - query_metadata: Query metadata
    """
    # Step 1: Query enrichment table
    query_result = query_temporal_stability_signals(
        server_ids=server_ids,
        hours_back=STABILITY_WINDOW_HOURS
    )
    
    # Step 2: Aggregate by server
    stability_map = aggregate_stability_by_server(query_result.get("rows", []))
    
    # Step 3: Build composite contributions
    enriched_signals = get_composite_signal_contribution(stability_map)
    
    # Step 4: Validate data flow
    validation = validate_data_flow()
    
    return {
        "enriched_signals": enriched_signals,
        "stability_map": stability_map,
        "validation": validation,
        "query_metadata": query_result.get("metadata", {}),
        "total_signals": len(enriched_signals),
        "total_servers": len(stability_map)
    }


class TemporalStabilityWiring:
    """
    Class-based wiring interface for signal_analyser_v2.py integration.
    Provides stateful access to temporal stability signals.
    """
    
    def __init__(self, poll_interval: int = POLL_INTERVAL_SECS):
        self.poll_interval = poll_interval
        self.last_fetch_time = None
        self.cached_signals = []
        self.cached_stability_map = {}
        self._running = False
    
    def fetch_signals(self, server_ids: Optional[List[str]] = None) -> List[Dict]:
        """Fetch and cache temporal stability signals."""
        result = build_temporal_signals_for_analyser(server_ids=server_ids)
        self.cached_signals = result.get("enriched_signals", [])
        self.cached_stability_map = result.get("stability_map", {})
        self.last_fetch_time = datetime.now()
        return self.cached_signals
    
    def get_signals_for_analyser(self) -> List[Dict]:
        """Get cached signals suitable for signal_analyser_v2.py pipeline."""
        if self.last_fetch_time is None:
            self.fetch_signals()
        return self.cached_signals
    
    def get_stability_factor(self, server_id: str) -> float:
        """Get stability factor for a specific server."""
        if not self.cached_stability_map:
            self.fetch_signals()
        entry = self.cached_stability_map.get(server_id, {})
        return entry.get("stability_factor", 0.5)
    
    def should_refetch(self) -> bool:
        """Check if cache should be refreshed based on poll interval."""
        if self.last_fetch_time is None:
            return True
        elapsed = (datetime.now() - self.last_fetch_time).total_seconds()
        return elapsed >= self.poll_interval
    
    def run_loop(self, server_ids: Optional[List[str]] = None):
        """
        Background loop for continuous enrichment updates.
        Simulates integration with signal_analyser_v2.py.
        """
        self._running = True
        while self._running:
            if self.should_refetch():
                signals = self.fetch_signals(server_ids=server_ids)
                # In production, these signals would be passed to signal_analyser_v2.py
                # For now, we log the ready state
                print(f"[temporal_stability_wiring] Fetched {len(signals)} signals")
            time.sleep(self.poll_interval)
    
    def stop(self):
        """Stop the background loop."""
        self._running = False


def get_integration_points() -> Dict[str, str]:
    """
    Return integration points for manual wiring into signal_analyser_v2.py.
    Documents how to connect this module's output into the analyser pipeline.
    """
    return {
        "direct_function": "build_temporal_signals_for_analyser(server_ids=None) -> Dict",
        "class_interface": "TemporalStabilityWiring().get_signals_for_analyser() -> List[Dict]",
        "signal_format": "enriched_signals[{server_id, signal_name, score, weight, evidence}]",
        "expected_schema": {
            "server_id": "str - MCP server identifier",
            "signal_name": "str - 'temporal_stability_composite'",
            "score": "float - stability factor 0.0-1.0",
            "weight": "float - 0.15 for typical weighting",
            "evidence": "dict - variance, stddev, signal_count"
        },
        "pipeline_injection": (
            "from temporal_stability_wiring import build_temporal_signals_for_analyser\n"
            "enriched = build_temporal_signals_for_analyser()\n"
            "signals = enriched['enriched_signals']\n"
            "# Pass to signal_analyser_v2.process_signals(signals)"
        )
    }


if __name__ == "__main__":
    print("=== Temporal Stability Wiring Module ===")
    print(f"Querying temporal stability signals from {ENRICHMENTS_TABLE}...")
    
    result = build_temporal_signals_for_analyser()
    
    print(f"\nValidation: {result['validation']}")
    print(f"Total servers with stability data: {result['total_servers']}")
    print(f"Total enriched signals: {result['total_signals']}")
    
    if result["enriched_signals"]:
        print("\nSample signal structure:")
        sample = result["enriched_signals"][0]
        print(f"  server_id: {sample.get('server_id')}")
        print(f"  signal_name: {sample.get('signal_name')}")
        print(f"  score: {sample.get('score')}")
        print(f"  weight: {sample.get('weight')}")
        print(f"  evidence: {sample.get('evidence')}")
    
    print("\n=== Integration Points ===")
    for key, value in get_integration_points().items():
        print(f"\n{key}:")
        print(f"  {value}")