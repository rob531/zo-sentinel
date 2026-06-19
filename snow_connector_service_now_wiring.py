import os
import requests
import time
import hmac
import hashlib
import json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configuration
WRITE_SERVICE_URL = "http://localhost:8772"
SNOW_API_URL = os.environ.get("SNOW_API_ENDPOINT")
SNOW_WEBHOOK_SECRET = os.environ.get("SNOW_WEBHOOK_SECRET")
SNOW_TOKEN = os.environ.get("SNOW_OAUTH_TOKEN")

def get_retry_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    return session

def validate_signature(payload, signature):
    mac = hmac.new(SNOW_WEBHOOK_SECRET.encode(), payload, hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), signature)

def fetch_pending_decisions():
    response = requests.post(f"{WRITE_SERVICE_URL}/query", json={
        "query": "SELECT * FROM mcp_decisions WHERE status = 'PENDING'"
    })
    return response.json() if response.status_code == 200 else []

def log_audit(action, details):
    requests.post(f"{WRITE_SERVICE_URL}/write", json={
        "table": "audit_log",
        "data": {"action": action, "details": json.dumps(details), "timestamp": time.time()}
    })

def process_verdict(decision):
    risk_level = decision.get("risk_level")
    
    # Constraint 5: No auto-commit for restricted levels
    if risk_level in ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED"]:
        return {"status": "HELD", "reason": "Manual review required"}

    # Constraint 3 & 7: POST to SNOW with timeout/backoff
    payload = {"decision_id": decision["id"], "verdict": decision["verdict"]}
    headers = {"Authorization": f"Bearer {SNOW_TOKEN}", "Content-Type": "application/json"}
    
    session = get_retry_session()
    response = session.post(f"{SNOW_API_URL}/verdict", json=payload, headers=headers, timeout=10)
    
    # Constraint 6: Audit log
    log_audit("SNOW_OUTBOUND_VERDICT", {"id": decision["id"], "code": response.status_code})
    
    return response.json()

def run_approval_loop():
    while True:
        decisions = fetch_pending_decisions()
        for decision in decisions:
            process_verdict(decision)
        time.sleep(30)