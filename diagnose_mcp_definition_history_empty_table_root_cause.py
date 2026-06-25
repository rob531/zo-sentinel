import requests
import json
from datetime import datetime, timedelta

def query_write_service(query):
    url = "http://127.0.0.1:8772/query"
    headers = {"Content-Type": "application/json"}
    payload = {"query": query}
    response = requests.post(url, headers=headers, data=json.dumps(payload))
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error querying write_service: {response.status_code} - {response.text}")
        return None

def check_mcp_definition_history():
    query = """
    SELECT COUNT(*) as count FROM mcp_definition_history
    """
    result = query_write_service(query)
    if result and result.get("data"):
        count = result["data"][0]["count"]
        print(f"mcp_definition_history table has {count} entries.")
        return count > 0
    return False

def check_mcp_server_registry():
    query = """
    SELECT COUNT(*) as count FROM mcp_server_registry
    WHERE last_updated > NOW() - INTERVAL '1 hour'
    """
    result = query_write_service(query)
    if result and result.get("data"):
        count = result["data"][0]["count"]
        print(f"mcp_server_registry has {count} active entries in the last hour.")
        return count > 0
    return False

def check_service_health():
    query = """
    SELECT status, last_heartbeat FROM service_health
    WHERE service_name = 'mcp_definition_history_populator'
    """
    result = query_write_service(query)
    if result and result.get("data"):
        data = result["data"][0]
        status = data["status"]
        last_heartbeat = data["last_heartbeat"]
        print(f"mcp_definition_history_populator status: {status}")
        print(f"Last heartbeat: {last_heartbeat}")
        return status == "healthy" and (datetime.now() - datetime.fromisoformat(last_heartbeat)).total_seconds() < 300
    return False

def call_populator_main_logic():
    try:
        # Assuming the populator exposes a /process endpoint for testing
        url = "http://127.0.0.1:8773/process"
        dummy_input = {"dummy": "input"}
        response = requests.post(url, json=dummy_input)
        if response.status_code == 200:
            print("Populator main logic executed successfully.")
            return True
        else:
            print(f"Error calling populator main logic: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Exception calling populator main logic: {e}")
        return False

def diagnose_empty_table():
    print("Starting diagnosis for empty mcp_definition_history table...")

    has_history = check_mcp_definition_history()
    has_active_servers = check_mcp_server_registry()
    is_populator_healthy = check_service_health()

    print("\nDiagnostic Report:")
    print("-----------------")

    if not has_history:
        print("1. mcp_definition_history table is empty.")
    else:
        print("1. mcp_definition_history table has entries (diagnosis not needed).")
        return

    if not has_active_servers:
        print("2. No active MCP servers found in mcp_server_registry in the last hour.")
    else:
        print("2. Active MCP servers found in mcp_server_registry.")

    if not is_populator_healthy:
        print("3. mcp_definition_history_populator is not healthy or hasn't heartbeat recently.")
    else:
        print("3. mcp_definition_history_populator is healthy and active.")

        # Attempt to call populator's main logic if it's healthy
        if call_populator_main_logic():
            print("4. Populator's main logic executed successfully with dummy input.")
        else:
            print("4. Failed to execute populator's main logic with dummy input.")

    print("\nPotential Reasons for Empty Table:")
    print("--------------------------------")
    if not has_active_servers:
        print("- No active MCP servers to process definitions.")
    if not is_populator_healthy:
        print("- Populator service is not running or not healthy.")
    else:
        print("- Populator is running but might not be processing new entries.")
        print("- There might be no new MCP definitions to process.")
        print("- There might be errors in the populator's processing logic.")

if __name__ == "__main__":
    diagnose_empty_table()