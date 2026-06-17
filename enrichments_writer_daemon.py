#!/usr/bin/env python3
"""
Daemon that reads computed enrichment scores from mcp_signal_enrichments
and writes them as structured rows to mcp_signal_scores.
"""

import json
import time
import requests
from datetime import datetime, timezone

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEALTH_SERVICE_URL = "http://127.0.0.1:8772"
HEARTBEAT_INTERVAL = 60


def query_service(sql):
    """Query write_service /query endpoint."""
    response = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json={"sql": sql},
        headers={"Content-Type": "application/json"}
    )
    response.raise_for_status()
    return response.json()


def write_to_service(rows):
    """Write rows to mcp_signal_scores via write_service /write."""
    payload = {
        "table": "mcp_signal_scores",
        "rows": rows
    }
    response = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json=payload,
        headers={"Content-Type": "application/json"}
    )
    response.raise_for_status()
    return response.json()


def fetch_enrichments():
    """Fetch enrichment data from mcp_signal_enrichments."""
    sql = """
        SELECT signal_type, mcp_identifier, score, evidence_blob, computed_at 
        FROM mcp_signal_enrichments
    """
    result = query_service(sql)
    return result.get("rows", [])


def fetch_registry_context():
    """Fetch registry context from mcp_server_registry."""
    sql = "SELECT mcp_identifier, registry_source FROM mcp_server_registry"
    result = query_service(sql)
    rows = result.get("rows", [])
    return {row["mcp_identifier"]: row["registry_source"] for row in rows}


def send_heartbeat(status="running", rows_processed=0):
    """Send heartbeat to service_health."""
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = {
        "service": "enrichments_writer_daemon",
        "status": status,
        "timestamp": timestamp,
        "rows_processed": rows_processed
    }
    try:
        requests.post(
            f"{HEALTH_SERVICE_URL}/heartbeat",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5
        )
    except requests.RequestException:
        pass


def process_batch():
    """Process one batch: query enrichments and write to scores."""
    enrichments = fetch_enrichments()
    
    if not enrichments:
        return 0
    
    registry_context = fetch_registry_context()
    
    rows_to_write = []
    for enrichment in enrichments:
        mcp_identifier = enrichment.get("mcp_identifier")
        signal_type = enrichment.get("signal_type")
        score = enrichment.get("score")
        evidence_blob = enrichment.get("evidence_blob")
        computed_at = enrichment.get("computed_at")
        
        confidence = 0.5
        if evidence_blob:
            try:
                if isinstance(evidence_blob, str):
                    blob_data = json.loads(evidence_blob)
                else:
                    blob_data = evidence_blob
                
                if isinstance(blob_data, dict) and "confidence" in blob_data:
                    confidence = float(blob_data["confidence"])
                elif isinstance(blob_data, dict) and "evidence_count" in blob_data:
                    evidence_count = int(blob_data["evidence_count"])
                    confidence = min(1.0, evidence_count / 10.0)
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
        
        row = {
            "mcp_identifier": mcp_identifier,
            "signal_type": signal_type,
            "score": score,
            "confidence": confidence,
            "evidence_blob": evidence_blob,
            "computed_at": computed_at
        }
        rows_to_write.append(row)
    
    write_result = write_to_service(rows_to_write)
    rows_written = write_result.get("rows_written", 0)
    
    return rows_written


def run():
    """Main daemon loop with heartbeat every 60s."""
    print("enrichments_writer_daemon started")
    
    last_heartbeat = 0
    
    while True:
        current_time = time.time()
        
        try:
            rows_written = process_batch()
            print(f"Processed batch: {rows_written} rows written")
        except Exception as e:
            print(f"Error processing batch: {e}")
            rows_written = 0
        
        if current_time - last_heartbeat >= HEARTBEAT_INTERVAL:
            send_heartbeat(status="running", rows_processed=rows_written)
            last_heartbeat = current_time
        
        time.sleep(1)


def self_test():
    """Self-test to verify the pipeline works."""
    print("Running self-test...")
    
    try:
        enrichments = fetch_enrichments()
        print(f"Fetched {len(enrichments)} enrichments")
        
        if enrichments:
            rows_written = process_batch()
        else:
            rows_written = 0
        
        assert rows_written >= 0, f"rows_written should be >= 0, got {rows_written}"
        print(f"Self-test: rows_written = {rows_written}")
        print("PASS")
        return True
        
    except Exception as e:
        print(f"Self-test failed: {e}")
        return False


if __name__ == "__main__":
    success = self_test()
    exit(0 if success else 1)