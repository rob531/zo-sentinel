import requests
import time
import logging
from datetime import datetime

# Configuration
WRITE_SERVICE_URL = "http://localhost:8772/verdict"
TIMEOUT = 10
MAX_RETRIES = 3

def get_verdict_with_retry(payload):
    """Query mcp_server_registry.verdict with exponential backoff."""
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            if attempt == MAX_RETRIES - 1:
                raise e
            time.sleep(2 ** attempt)

def audit_log(server_id, verdict, decision):
    """Section 7: Audit requirements."""
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "server_id": server_id,
        "verdict": verdict,
        "decision": decision
    }
    logging.info(f"AUDIT_LOG: {log_entry}")

def process_commit(commit_payload, override_flag=False):
    """Phase 9 Integration: Core Loop."""
    server_id = commit_payload.get("server_id")
    
    # 1. Query verdict (No caching)
    verdict_data = get_verdict_with_retry({"server_id": server_id})
    verdict = verdict_data.get("verdict")
    
    # 2. Risk evaluation
    restricted_verdicts = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED"]
    if verdict in restricted_verdicts and not override_flag:
        audit_log(server_id, verdict, "REJECTED")
        raise PermissionError(f"Commit rejected: {verdict} status.")
    
    # 3. Inject resilience score
    resilience_score = verdict_data.get("mcp_signal_scores", {}).get("injection_resilience")
    commit_payload["injection_resilience"] = resilience_score
    
    # 4. Finalize
    audit_log(server_id, verdict, "APPROVED")
    return commit_payload