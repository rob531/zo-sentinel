import requests
import time
import subprocess
from typing import Dict, Any

def verify_refresher() -> bool:
    """Verify the attestation refresher functionality by checking if attestations are correctly refreshed."""
    try:
        # Query the mcp_attestations table to get a sample attestation
        query = """
        SELECT * FROM mcp_attestations
        LIMIT 1
        """
        response = requests.post("http://127.0.0.1:8772/query", json={"query": query})
        response.raise_for_status()
        data = response.json()

        if not data or not data['data']:
            print("No attestations found in mcp_attestations table.")
            return False

        attestation = data['data'][0]
        original_expiry = attestation['expiry']

        # Simulate the effect of the attestation refresher by calling it directly if possible
        try:
            subprocess.run(["python", "attestation_refresher.py"], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Failed to run attestation_refresher.py: {e}")
            return False

        # Wait for a short period to allow the refresher to complete
        time.sleep(2)

        # Query the mcp_attestations table again to check if the attestation was refreshed
        response = requests.post("http://127.0.0.1:8772/query", json={"query": query})
        response.raise_for_status()
        data = response.json()

        if not data or not data['data']:
            print("No attestations found in mcp_attestations table after refresh.")
            return False

        refreshed_attestation = data['data'][0]
        refreshed_expiry = refreshed_attestation['expiry']

        # Check if the expiry time has been updated
        if refreshed_expiry <= original_expiry:
            print("Attestation expiry time was not refreshed correctly.")
            return False

        return True

    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return False

if __name__ == "__main__":
    if verify_refresher():
        print("PASS")
    else:
        print("FAIL")