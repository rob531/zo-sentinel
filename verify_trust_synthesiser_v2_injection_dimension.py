from fastapi import FastAPI, HTTPException
import uvicorn
import time
import requests
import os
import signal
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import logging

SERVICE_NAME = "verify_trust_synthesiser_v2_injection_dimension"
PORT = 8795
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 30

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(SERVICE_NAME)

app = FastAPI()

TEST_CONFIG = {
    "dimension": "injection_resilience",
    "expected_weight": 1.6,
    "threshold": 0.80,
    "min_score": 0.0,
    "max_score": 100.0,
    "min_test_records": 3
}


def send_heartbeat():
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.utcnow().isoformat()
            },
            "wait": True
        }
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


def query_mcp_signal_scores(dimension: str, limit: int = 3) -> List[Dict]:
    query = f"""
    SELECT target_server_id, score, weight, dimension, recorded_at
    FROM mcp_signal_scores
    WHERE dimension = '{dimension}'
    ORDER BY recorded_at DESC
    LIMIT {limit}
    """
    try:
        response = requests.post(QUERY_URL, json={"query": query}, timeout=10)
        if response.status_code == 200:
            result = response.json()
            return result.get("data", [])
        else:
            logger.error(f"Query failed: {response.status_code} - {response.text}")
            return []
    except Exception as e:
        logger.error(f"Query error: {e}")
        return []


def verify_injection_resilience_dimension() -> Dict[str, Any]:
    results = {
        "test_name": "verify_trust_synthesiser_v2_injection_dimension",
        "dimension": TEST_CONFIG["dimension"],
        "expected_weight": TEST_CONFIG["expected_weight"],
        "threshold": TEST_CONFIG["threshold"],
        "status": "PENDING",
        "records_found": 0,
        "scores_valid": [],
        "scores_invalid": [],
        "weighted_scores": [],
        "records_above_threshold": 0,
        "errors": []
    }
    
    logger.info(f"Testing injection_resilience dimension with weight={TEST_CONFIG['expected_weight']}, threshold={TEST_CONFIG['threshold']}")
    
    records = query_mcp_signal_scores(TEST_CONFIG["dimension"], TEST_CONFIG["min_test_records"])
    results["records_found"] = len(records)
    
    if len(records) < TEST_CONFIG["min_test_records"]:
        results["errors"].append(f"Expected at least {TEST_CONFIG['min_test_records']} records, found {len(records)}")
    
    for record in records:
        try:
            target_server_id = record.get("target_server_id")
            score = float(record.get("score", -1))
            
            if TEST_CONFIG["min_score"] <= score <= TEST_CONFIG["max_score"]:
                results["scores_valid"].append({
                    "server_id": target_server_id,
                    "score": score
                })
                
                weighted_score = score * TEST_CONFIG["expected_weight"]
                results["weighted_scores"].append({
                    "server_id": target_server_id,
                    "raw_score": score,
                    "weight": TEST_CONFIG["expected_weight"],
                    "weighted_score": weighted_score
                })
                
                if weighted_score >= TEST_CONFIG["threshold"]:
                    results["records_above_threshold"] += 1
            else:
                results["scores_invalid"].append({
                    "server_id": target_server_id,
                    "score": score,
                    "reason": f"Score must be in [{TEST_CONFIG['min_score']}, {TEST_CONFIG['max_score']}]"
                })
                
        except (ValueError, TypeError) as e:
            results["errors"].append(f"Invalid record format: {record} - {e}")
    
    if results["scores_invalid"]:
        results["status"] = "FAILED"
        results["errors"].append(f"{len(results['scores_invalid'])} records have invalid scores")
    elif results["records_found"] >= TEST_CONFIG["min_test_records"] and len(results["scores_valid"]) >= TEST_CONFIG["min_test_records"]:
        results["status"] = "PASSED"
        logger.info(f"Verification PASSED: Found {len(results['scores_valid'])} valid injection_resilience records")
    else:
        results["status"] = "FAILED"
        results["errors"].append("Insufficient valid records for verification")
    
    return results


@app.get("/health")
def health():
    return {"status": "healthy", "service": SERVICE_NAME}


@app.get("/verify")
def verify():
    results = verify_injection_resilience_dimension()
    return results


@app.post("/heartbeat")
def heartbeat():
    send_heartbeat()
    return {"status": "heartbeat_sent"}


def run():
    send_heartbeat()
    
    import threading
    def heartbeat_loop():
        while True:
            time.sleep(HEARTBEAT_INTERVAL)
            send_heartbeat()
    
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    logger.info(f"{SERVICE_NAME} starting on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    run()