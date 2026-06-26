import requests
import re

def run_verification() -> bool:
    # Test MCP ID with known data in the database
    test_mcp_id = "MCP-12345"

    try:
        # Fetch the MCP detail view HTML
        html_response = requests.get(f"http://localhost:5000/mcp_detail_view.html?mcp_id={test_mcp_id}")
        html_response.raise_for_status()
        html_content = html_response.text

        # Fetch the API data for the same MCP ID
        api_response = requests.get(f"http://localhost:5000/mcp_verdict_detail_api.py?mcp_id={test_mcp_id}")
        api_response.raise_for_status()
        api_data = api_response.json()

        # Check if the HTML contains the MCP ID
        if f"MCP ID: {test_mcp_id}" not in html_content:
            print(f"FAIL: MCP ID {test_mcp_id} not found in HTML content.")
            return False

        # Check if the HTML contains the verdict
        verdict = api_data.get("verdict", "").strip()
        if verdict and f"Verdict: {verdict}" not in html_content:
            print(f"FAIL: Verdict '{verdict}' not found in HTML content.")
            return False

        # Check if the HTML contains the signals
        signals = api_data.get("signals", [])
        for signal in signals:
            if f"Signal: {signal}" not in html_content:
                print(f"FAIL: Signal '{signal}' not found in HTML content.")
                return False

        # Check if the HTML contains the attestations
        attestations = api_data.get("attestations", [])
        for attestation in attestations:
            if f"Attestation: {attestation}" not in html_content:
                print(f"FAIL: Attestation '{attestation}' not found in HTML content.")
                return False

        print("PASS: MCP detail view integration is successful.")
        return True

    except requests.exceptions.RequestException as e:
        print(f"FAIL: Request error - {e}")
        return False
    except Exception as e:
        print(f"FAIL: Unexpected error - {e}")
        return False

if __name__ == "__main__":
    assert run_verification(), "Verification failed."
    print("PASS")