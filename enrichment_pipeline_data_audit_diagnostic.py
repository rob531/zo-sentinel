#!/usr/bin/env python3
"""
enrichment_pipeline_data_audit_diagnostic.py

Diagnostic FastAPI utility that queries write_service for current state of
mcp_signal_enrichments vs mcp_signal_scores, and surfaces why the enrichment
table has only ~12 rows (expected: thousands).

Bridges the gap between built enricher modules and the empty enrichments table
by diagnosing whether the write_service staleness is the root cause blocking
enrichment writes.
"""

import json
import time
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

# Configuration
WRITE_SERVICE_URL = "http://127.127.0.1:8772/query"
APP_PORT = 8796

# Thresholds for diagnosis
STALE_THRESHOLD_SECONDS = 3600  # 1 hour
ENRICHMENTS_EXPECTED_MIN = 100  # Minimum expected rows (thousands expected, use 100 as sanity check)
SCORES_BASELINE_MIN = 1000  # Scores table should have significant data

# Enricher module detection - these are the expected enricher modules
# In production, this could be discovered dynamically from the codebase
EXPECTED_ENRICHER_MODULES = [
    "geo_enricher",
    "temporal_enricher",
    "entity_resolution_enricher",
    "contextual_enricher",
    "anomaly_enricher",
    "correlation_enricher",
    "threat_intel_enricher",
    "behavioral_enricher",
]


app = FastAPI(
    title="Enrichment Pipeline Data Audit Diagnostic",
    description="Diagnostic utility for enrichment pipeline state and write_service health",
    version="1.0.0",
)


def query_write_service(query: str) -> dict[str, Any] | None:
    """
    Query the write_service health endpoint.
    Returns None if the service is unreachable.
    """
    try:
        response = requests.get(
            WRITE_SERVICE_URL,
            params={"query": query},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        return None


def get_enrichments_count() -> int | None:
    """Get current row count from mcp_signal_enrichments."""
    result = query_write_service("SELECT COUNT(*) FROM mcp_signal_enrichments")
    if result and "rows" in result and len(result["rows"]) > 0:
        return result["rows"][0][0]
    return None


def get_last_enrichment_timestamp() -> str | None:
    """Get the most recent enrichment write timestamp."""
    result = query_write_service("SELECT MAX(computed_at) FROM mcp_signal_enrichments")
    if result and "rows" in result and len(result["rows"]) > 0:
        return result["rows"][0][0]
    return None


def get_enrichment_signal_type_count() -> int | None:
    """Get count of distinct signal types in enrichments."""
    result = query_write_service(
        "SELECT COUNT(DISTINCT signal_type) FROM mcp_signal_enrichments"
    )
    if result and "rows" in result and len(result["rows"]) > 0:
        return result["rows"][0][0]
    return None


def get_scores_count() -> int | None:
    """Get current row count from mcp_signal_scores."""
    result = query_write_service("SELECT COUNT(*) FROM mcp_signal_scores")
    if result and "rows" in result and len(result["rows"]) > 0:
        return result["rows"][0][0]
    return None


def get_enricher_signal_counts() -> dict[str, int]:
    """
    Get per-enricher row counts from mcp_signal_enrichments.
    Returns dict mapping enricher module name to row count.
    """
    enricher_counts = {}
    
    # Query for signal_type counts - assuming signal_type maps to enricher modules
    result = query_write_service(
        "SELECT signal_type, COUNT(*) FROM mcp_signal_enrichments GROUP BY signal_type"
    )
    
    if result and "rows" in result:
        for row in result["rows"]:
            signal_type = row[0] if row[0] else "unknown"
            count = row[1]
            enricher_counts[signal_type] = count
    
    return enricher_counts


def calculate_stale_age_seconds(last_write_timestamp: str | None) -> int | None:
    """
    Calculate how many seconds the write_service has been stale.
    Returns None if no timestamp available.
    """
    if last_write_timestamp is None:
        return None
    
    try:
        # Handle various timestamp formats
        if isinstance(last_write_timestamp, str):
            # Try ISO format first
            try:
                last_dt = datetime.fromisoformat(last_write_timestamp.replace("Z", "+00:00"))
            except ValueError:
                # Try parsing as epoch
                return None
        else:
            return None
        
        now = datetime.now(timezone.utc)
        delta = now - last_dt
        return int(delta.total_seconds())
    except (ValueError, TypeError):
        return None


def detect_enricher_modules(enricher_counts: dict[str, int]) -> list[str]:
    """
    Detect which enricher modules are present based on signal types in data.
    Returns list of detected module names.
    """
    detected = []
    
    # Check for known enricher patterns in signal_type values
    for signal_type in enricher_counts.keys():
        for module in EXPECTED_ENRICHER_MODULES:
            if module.lower() in str(signal_type).lower():
                if module not in detected:
                    detected.append(module)
    
    # If no matches found, check if any signal types exist at all
    if not detected and enricher_counts:
        # Assume all signal types represent distinct enrichers
        detected = [f"enricher_{i}" for i in range(len(enricher_counts))]
    
    return detected


def determine_diagnosis(
    enrichments_count: int | None,
    scores_count: int | None,
    stale_age_seconds: int | None,
    enricher_counts: dict[str, int],
    write_service_reachable: bool,
) -> str:
    """
    Determine the root cause diagnosis based on collected metrics.
    """
    # Check if write_service is reachable
    if not write_service_reachable or enrichments_count is None:
        return "write_service_down"
    
    # Check for write_service staleness
    if stale_age_seconds is not None and stale_age_seconds > STALE_THRESHOLD_SECONDS:
        return "write_service_down"
    
    # Check if scores table has data (prerequisite for enrichment)
    if scores_count is not None and scores_count < SCORES_BASELINE_MIN:
        return "enrichers_unwired"
    
    # Check if enricher modules are producing data
    if enrichments_count == 0:
        # No enrichments at all
        if scores_count and scores_count > 0:
            return "enrichers_unwired"
        return "enricher_compute_failure"
    
    # Check if enrichments are significantly below expected
    if enrichments_count < ENRICHMENTS_EXPECTED_MIN:
        # Some enrichments exist but far fewer than expected
        if not enricher_counts or len(enricher_counts) < 3:
            return "enricher_compute_failure"
        return "enrichers_unwired"
    
    return "ok"


@app.get("/audit/enrichment_pipeline_status")
def get_enrichment_pipeline_status() -> JSONResponse:
    """
    Returns JSON diagnostic information about the enrichment pipeline state.
    """
    # Collect all metrics
    enrichments_count = get_enrichments_count()
    scores_count = get_scores_count()
    last_timestamp = get_last_enrichment_timestamp()
    signal_type_count = get_enrichment_signal_type_count()
    enricher_counts = get_enricher_signal_counts()
    
    # Determine if write_service is reachable
    write_service_reachable = query_write_service("SELECT 1") is not None
    
    # Calculate stale age
    stale_age_seconds = calculate_stale_age_seconds(last_timestamp)
    
    # Detect enricher modules
    enricher_modules_detected = detect_enricher_modules(enricher_counts)
    
    # Determine diagnosis
    diagnosis = determine_diagnosis(
        enrichments_count=enrichments_count,
        scores_count=scores_count,
        stale_age_seconds=stale_age_seconds,
        enricher_counts=enricher_counts,
        write_service_reachable=write_service_reachable,
    )
    
    # Build response
    response_data = {
        "enrichments_row_count": enrichments_count if enrichments_count is not None else -1,
        "scores_row_count": scores_count if scores_count is not None else -1,
        "enricher_modules_detected": enricher_modules_detected,
        "last_enrichment_write_timestamp": last_timestamp,
        "write_service_stale_age_seconds": stale_age_seconds if stale_age_seconds is not None else -1,
        "diagnosis": diagnosis,
        "metadata": {
            "signal_type_count": signal_type_count if signal_type_count is not None else -1,
            "enricher_counts": enricher_counts,
            "write_service_reachable": write_service_reachable,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        },
    }
    
    return JSONResponse(content=response_data)


@app.get("/audit/enricher_module_status")
def get_enricher_module_status() -> JSONResponse:
    """
    Lists each built enricher module and whether it has produced at least one row.
    """
    enricher_counts = get_enricher_signal_counts()
    enrichments_count = get_enrichments_count()
    
    # Build status for each expected enricher module
    module_status = []
    for module in EXPECTED_ENRICHER_MODULES:
        has_rows = False
        row_count = 0
        
        # Check if any signal type matches this module
        for signal_type, count in enricher_counts.items():
            if module.lower() in str(signal_type).lower():
                has_rows = True
                row_count = count
                break
        
        module_status.append({
            "module_name": module,
            "has_produced_rows": has_rows,
            "row_count": row_count,
            "status": "active" if has_rows else "inactive",
        })
    
    # Add any detected modules not in expected list
    detected_extra = set()
    for signal_type in enricher_counts.keys():
        matched = False
        for module in EXPECTED_ENRICHER_MODULES:
            if module.lower() in str(signal_type).lower():
                matched = True
                break
        if not matched:
            detected_extra.add(signal_type)
    
    for extra_signal in detected_extra:
        module_status.append({
            "module_name": f"unknown_{extra_signal}",
            "has_produced_rows": enricher_counts[extra_signal] > 0,
            "row_count": enricher_counts[extra_signal],
            "status": "detected" if enricher_counts[extra_signal] > 0 else "inactive",
        })
    
    response_data = {
        "total_expected_modules": len(EXPECTED_ENRICHER_MODULES),
        "total_detected_modules": len(module_status),
        "total_enrichments": enrichments_count if enrichments_count is not None else 0,
        "modules": module_status,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    
    return JSONResponse(content=response_data)


@app.get("/health")
def health_check() -> JSONResponse:
    """Simple health check endpoint."""
    return JSONResponse(content={"status": "healthy", "service": "enrichment_pipeline_audit"})


def run_diagnostic() -> dict[str, Any]:
    """
    Run the diagnostic by calling the local endpoint.
    Returns the diagnostic response data.
    """
    local_url = f"http://127.0.0.1:{APP_PORT}"
    response = requests.get(f"{local_url}/audit/enrichment_pipeline_status", timeout=10)
    response.raise_for_status()
    return response.json()


def print_diagnosis(diagnostic: dict[str, Any]) -> None:
    """Print a human-readable diagnosis line."""
    diagnosis = diagnostic.get("diagnosis", "unknown")
    enrichments_count = diagnostic.get("enrichments_row_count", -1)
    scores_count = diagnostic.get("scores_row_count", -1)
    stale_age = diagnostic.get("write_service_stale_age_seconds", -1)
    
    diagnosis_messages = {
        "write_service_down": (
            f"WRITE_SERVICE_DOWN: write_service is unreachable or has been stale "
            f"for {stale_age} seconds. Enrichments table has {enrichments_count} rows. "
            f"Action: Restart write_service or investigate network connectivity."
        ),
        "enrichers_unwired": (
            f"ENRICHERS_UNWIRED: Enricher modules are not connected to the write pipeline. "
            f"Found {enrichments_count} enrichment rows vs {scores_count} score rows. "
            f"Action: Check enricher module wiring and signal routing configuration."
        ),
        "enricher_compute_failure": (
            f"ENRICHER_COMPUTE_FAILURE: Enricher modules exist but failed to produce output. "
            f"Only {enrichments_count} rows in enrichments table. "
            f"Action: Check enricher logs for computation errors or resource constraints."
        ),
        "ok": (
            f"OK: Enrichment pipeline healthy. "
            f"Found {enrichments_count} enrichment rows and {scores_count} score rows. "
            f"Pipeline is functioning normally."
        ),
    }
    
    message = diagnosis_messages.get(
        diagnosis,
        f"UNKNOWN_DIAGNOSIS ({diagnosis}): Unable to determine root cause."
    )
    
    print(f"[DIAGNOSIS] {message}")
    print(f"[DETAILS] enrichments_row_count={enrichments_count}, scores_row_count={scores_count}")
    print(f"[DETAILS] write_service_stale_age_seconds={stale_age}")
    print(f"[DETAILS] enricher_modules_detected={diagnostic.get('enricher_modules_detected', [])}")


if __name__ == "__main__":
    import uvicorn
    from multiprocessing import Process
    
    print("=" * 60)
    print("Enrichment Pipeline Data Audit Diagnostic")
    print("=" * 60)
    print(f"Starting diagnostic server on port {APP_PORT}...")
    print(f"Write service endpoint: {WRITE_SERVICE_URL}")
    print()
    
    # Start the server in a subprocess
    server_process = Process(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": "127.0.0.1", "port": APP_PORT, "log_level": "error"},
    )
    server_process.start()
    
    # Give the server time to start
    time.sleep(2)
    
    try:
        # Run the diagnostic
        print("Running enrichment pipeline diagnostic...")
        print("-" * 40)
        
        diagnostic = run_diagnostic()
        
        # Assert acceptance criteria
        enrichments_row_count = diagnostic.get("enrichments_row_count")
        
        assert isinstance(enrichments_row_count, int), (
            f"Expected enrichments_row_count to be int, got {type(enrichments_row_count)}"
        )
        assert enrichments_row_count >= 0, (
            f"Expected enrichments_row_count >= 0, got {enrichments_row_count}"
        )
        
        print("-" * 40)
        print()
        
        # Print human-readable diagnosis
        print_diagnosis(diagnostic)
        
        print()
        print("=" * 60)
        print("Diagnostic completed successfully.")
        print(f"Full diagnostic data: {json.dumps(diagnostic, indent=2)}")
        print("=" * 60)
        
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Failed to connect to diagnostic server: {e}")
        server_process.terminate()
        exit(1)
    except AssertionError as e:
        print(f"ASSERTION FAILED: {e}")
        server_process.terminate()
        exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        server_process.terminate()
        exit(1)
    finally:
        server_process.terminate()
        server_process.join(timeout=5)
    
    exit(0)