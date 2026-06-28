import requests
import sys

def verify_mcp_definition_history_data_integrity_and_completeness():
    # Define the base URL for the write_service
    base_url = "http://write_service:5000"

    # Define the SQL queries
    queries = {
        "check_empty_table": "SELECT COUNT(*) FROM mcp_definition_history",
        "check_duplicate_entries": "SELECT mcp_id, timestamp, COUNT(*) FROM mcp_definition_history GROUP BY mcp_id, timestamp HAVING COUNT(*) > 1",
        "check_chronological_order": "SELECT mcp_id, timestamp FROM mcp_definition_history ORDER BY mcp_id, timestamp",
        "check_definition_hash_consistency": "SELECT mcp_id, definition_hash, COUNT(*) FROM mcp_definition_history GROUP BY mcp_id, definition_hash HAVING COUNT(*) > 1"
    }

    findings = []

    # Execute the queries and process the results
    for query_name, query in queries.items():
        response = requests.post(f"{base_url}/query", json={"query": query})
        if response.status_code != 200:
            findings.append(f"FAIL: Query execution failed for {query_name}")
            continue

        results = response.json()

        if query_name == "check_empty_table":
            if results[0][0] == 0:
                findings.append("CRITICAL: Table is empty")

        elif query_name == "check_duplicate_entries":
            if results:
                findings.append("FAIL: Duplicate entries found")

        elif query_name == "check_chronological_order":
            previous_mcp_id = None
            previous_timestamp = None
            for row in results:
                mcp_id, timestamp = row
                if mcp_id == previous_mcp_id and timestamp < previous_timestamp:
                    findings.append("FAIL: Chronological order violated")
                    break
                previous_mcp_id = mcp_id
                previous_timestamp = timestamp

        elif query_name == "check_definition_hash_consistency":
            if results:
                findings.append("FAIL: Inconsistent definition_hash found")

    # Print the findings
    if findings:
        for finding in findings:
            print(finding)
        sys.exit(1)
    else:
        print("PASS: Data integrity checks passed")
        sys.exit(0)

if __name__ == '__main__':
    verify_mcp_definition_history_data_integrity_and_completeness()