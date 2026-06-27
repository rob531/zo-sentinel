import argparse
import json
import requests
import sys

def get_mcp_details_interactive():
    """Interactively prompt for MCP details."""
    print("Enter MCP details:")
    mcp_name = input("MCP Name: ")
    requested_by = input("Requested By: ")
    mcp_data_json = input("MCP Data (JSON): ")

    try:
        # Validate JSON input
        json.loads(mcp_data_json)
    except json.JSONDecodeError:
        print("Invalid JSON provided for MCP data.")
        sys.exit(1)

    return {
        "mcp_name": mcp_name,
        "requested_by": requested_by,
        "mcp_data_json": mcp_data_json
    }

def construct_payload(mcp_details):
    """Construct the JSON payload for the MCP submission."""
    return {
        "table": "mcp_submissions",
        "data": mcp_details
    }

def submit_mcp(payload):
    """Submit the MCP payload to the write_service."""
    url = "http://127.0.0.1:8772/write"
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, data=json.dumps(payload), headers=headers)
        response.raise_for_status()  # Raise an exception for HTTP errors
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error submitting MCP: {e}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Manual MCP Submission Utility")
    parser.add_argument("--mcp-name", help="Name of the MCP")
    parser.add_argument("--requested-by", help="Person requesting the MCP")
    parser.add_argument("--mcp-data-json", help="MCP data in JSON format")

    args = parser.parse_args()

    if args.mcp_name and args.requested_by and args.mcp_data_json:
        mcp_details = {
            "mcp_name": args.mcp_name,
            "requested_by": args.requested_by,
            "mcp_data_json": args.mcp_data_json
        }
    else:
        mcp_details = get_mcp_details_interactive()

    payload = construct_payload(mcp_details)
    result = submit_mcp(payload)

    print("MCP Submission Result:")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    # Demonstration of submitting a sample MCP
    sample_mcp = {
        "mcp_name": "Sample MCP",
        "requested_by": "test_user",
        "mcp_data_json": json.dumps({"key": "value"})
    }

    print("Demonstrating sample MCP submission...")
    sample_payload = construct_payload(sample_mcp)
    sample_result = submit_mcp(sample_payload)

    print("Sample MCP Submission Result:")
    print(json.dumps(sample_result, indent=2))