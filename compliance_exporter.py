import json
from typing import List, Dict
import os
import requests
import time
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def export_compliance_report(output_path: str) -> None:
    url = "http://127.0.0.1:8772"
    headers = {"Content-Type": "application/json"}
    
    query_params = {
        "table": "mcp_server_registry",
        "rows": "",
        "wait": True
    }
    
    # Get all servers from MCP registry
    response = requests.post(f"{url}/write", json=query_params, headers=headers)
    if not response.ok:
        logger.error("Failed to get all servers")
        return
    
    server_list = response.json()["rows"]
    
    # Extract risk level and verdict for each server
    risks = []
    for server in server_list:
        response = requests.post(f"{url}/write", json=query_params, headers=headers)
        if not response.ok:
            logger.error("Failed to get MCP threat intel")
            break
        
        data = response.json()["rows"]
        verdicts = data["verdicts"]["mcp_verdicts"]
        
        # Assign risk level based on verdict
        if "insecure" in verdicts:
            risk_level = 3  # High Risk
        elif "medium" in verdicts or "low" in verdicts:
            risk_level = 2  # Medium Risk
        else:
            risk_level = 1  # Low Risk
        
        risks.append({
            "generated_at": datetime.now(),
            "total_assessed": len(server_list),
            "by_verdict": {
                "insecure": {"count": sum(1 for v in verdicts if v == "insecure")},
                "medium": {"count": sum(1 for v in verdicts if v == "medium")},
                "low": {"count": sum(1 for v in verdicts if v == "low")},
            },
            "high_risk_servers": [server["name"] for server in server_list],
        })
    
    # Write report to file
    with open(output_path, 'w') as f:
        json.dump(risks, f)
    
    # Schedule next run
    def schedule_next_run():
        time.sleep(60)  # Wait 1 minute before running again
        export_compliance_report(output_path)

    import threading
    threading.Thread(target=schedule_next_run).start()

# Example usage:
if __name__ == "__main__":
    output_path = "compliance_export.json"
    export_compliance_report(output_path)