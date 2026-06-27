#!/usr/bin/env python3

import requests
import sys
from datetime import datetime, timedelta

def verify_gate_orchestrator_heartbeat():
    try:
        # Calculate the threshold time (10 minutes ago)
        threshold_time = datetime.utcnow() - timedelta(minutes=10)

        # Query the write_service for gate_orchestrator's heartbeat
        query = """
        SELECT last_heartbeat
        FROM service_health
        WHERE service = 'gate_orchestrator'
        ORDER BY last_heartbeat DESC
        LIMIT 1
        """
        response = requests.get(
            "http://127.0.0.1:8772/query",
            params={"q": query},
            timeout=5
        )

        # Check for successful response
        if response.status_code != 200:
            print("FAIL")
            sys.exit(1)

        # Parse the response
        data = response.json()
        if not data or not data.get("results") or not data["results"][0].get("series"):
            print("FAIL")
            sys.exit(1)

        # Extract the last heartbeat time
        last_heartbeat = datetime.fromtimestamp(data["results"][0]["series"][0]["values"][0][0])

        # Verify if the heartbeat is recent
        if last_heartbeat >= threshold_time:
            print("PASS")
            sys.exit(0)
        else:
            print("FAIL")
            sys.exit(1)

    except requests.exceptions.RequestException:
        print("FAIL")
        sys.exit(1)

if __name__ == "__main__":
    verify_gate_orchestrator_heartbeat()