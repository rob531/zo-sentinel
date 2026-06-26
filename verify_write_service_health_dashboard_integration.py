import requests
import json
import sys
from datetime import datetime, timedelta

# --- Configuration ---
QUERY_ENDPOINT = "http://127.0.0.1:8772/query"
DASHBOARD_API_ENDPOINT = "http://127.0.0.1:8772/system_health_dashboard_api"
SERVICE_NAME = "write_service"
# Allow a small time difference (e.g., 5 seconds) for heartbeat timestamps
# to account for network latency or processing delays.
HEARTBEAT_TIME_DELTA_THRESHOLD = timedelta(seconds=5)

# --- Helper Functions ---

def get_db_service_health(service_name: str):
    """
    Queries the /query endpoint to get the health status of a specific service
    from the 'service_health' table.
    Expected /query response format:
    {"data": [{"service_name": "write_service", "status": "healthy", "last_heartbeat": "2023-10-27T10:00:00Z"}]}
    """
    query_payload = {
        "query": f"SELECT service_name, status, last_heartbeat FROM service_health WHERE service_name = '{service_name}'"
    }
    print(f"Attempting to query DB for '{service_name}' health via {QUERY_ENDPOINT}...")
    try:
        response = requests.post(QUERY_ENDPOINT, json=query_payload, timeout=5)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        data = response.json()

        if not data or 'data' not in data or not isinstance(data['data'], list) or not data['data']:
            print(f"WARN: No data or malformed 'data' field found for '{service_name}' in DB query response.")
            return None

        # Assuming the query returns a list of dictionaries, take the first one
        service_data = data['data'][0]
        db_status = service_data.get('status')
        db_last_heartbeat = service_data.get('last_heartbeat')

        if not db_status or not db_last_heartbeat:
            print(f"ERROR: Missing 'status' or 'last_heartbeat' in DB response for '{service_name}'.")
            print(f"  DB response content: {json.dumps(service_data)}")
            return None

        print(f"  DB Health for '{service_name}': Status='{db_status}', Last Heartbeat='{db_last_heartbeat}'")
        return db_status, db_last_heartbeat

    except requests.exceptions.ConnectionError:
        print(f"ERROR: Could not connect to {QUERY_ENDPOINT}. Is the server running at 127.0.0.1:8772?")
    except requests.exceptions.Timeout:
        print(f"ERROR: Request to {QUERY_ENDPOINT} timed out after 5 seconds.")
    except requests.exceptions.RequestException as e:
        print(f"ERROR: An HTTP error occurred while querying DB: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"  Response status code: {e.response.status_code}")
            print(f"  Response content: {e.response.text}")
    except json.JSONDecodeError:
        print(f"ERROR: Failed to decode JSON from DB query response. Response was: {response.text if 'response' in locals() else 'N/A'}")
    except IndexError:
        print(f"ERROR: DB query response 'data' field is empty or malformed for '{service_name}'.")
    return None

def get_dashboard_api_service_health(service_name: str):
    """
    Queries the system_health_dashboard_api endpoint to get the health status
    of a specific service as reported by the dashboard API.
    Expected dashboard API response format:
    {"services": [{"name": "write_service", "status": "healthy", "last_heartbeat": "2023-10-27T10:00:00Z"}]}
    """
    print(f"Attempting to query Dashboard API for '{service_name}' health via {DASHBOARD_API_ENDPOINT}...")
    try:
        response = requests.get(DASHBOARD_API_ENDPOINT, timeout=5)
        response.raise_for_status()
        data = response.json()

        if not data or 'services' not in data or not isinstance(data['services'], list):
            print(f"ERROR: Dashboard API response is malformed or missing 'services' key.")
            print(f"  API response content: {json.dumps(data)}")
            return None

        for service_entry in data['services']:
            if service_entry.get('name') == service_name:
                api_status = service_entry.get('status')
                api_last_heartbeat = service_entry.get('last_heartbeat')

                if not api_status or not api_last_heartbeat:
                    print(f"ERROR: Missing 'status' or 'last_heartbeat' in Dashboard API response for '{service_name}'.")
                    print(f"  Service entry content: {json.dumps(service_entry)}")
                    return None

                print(f"  Dashboard API Health for '{service_name}': Status='{api_status}', Last Heartbeat='{api_last_heartbeat}'")
                return api_status, api_last_heartbeat
        
        print(f"WARN: '{service_name}' not found in Dashboard API response.")
        print(f"  Available services: {[s.get('name') for s in data['services']]}")
        return None

    except requests.exceptions.ConnectionError:
        print(f"ERROR: Could not connect to {DASHBOARD_API_ENDPOINT}. Is the server running at 127.0.0.1:8772?")
    except requests.exceptions.Timeout:
        print(f"ERROR: Request to {DASHBOARD_API_ENDPOINT} timed out after 5 seconds.")
    except requests.exceptions.RequestException as e:
        print(f"ERROR: An HTTP error occurred while querying Dashboard API: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"  Response status code: {e.response.status_code}")
            print(f"  Response content: {e.response.text}")
    except json.JSONDecodeError:
        print(f"ERROR: Failed to decode JSON from Dashboard API response. Response was: {response.text if 'response' in locals() else 'N/A'}")
    return None

def compare_health_data(db_health, api_health):
    """
    Compares the health data retrieved from the database and the dashboard API.
    Allows for a small time delta for last_heartbeat.
    """
    if db_health is None:
        print("FAIL: Could not retrieve DB health data. Cannot verify integration.")
        return False
    if api_health is None:
        print("FAIL: Could not retrieve Dashboard API health data. Cannot verify integration.")
        return False

    db_status, db_heartbeat_str = db_health
    api_status, api_heartbeat_str = api_health

    # 1. Compare Status
    if db_status != api_status:
        print(f"FAIL: Status mismatch for '{SERVICE_NAME}'.")
        print(f"  DB Status: '{db_status}'")
        print(f"  API Status: '{api_status}'")
        return False
    print(f"  Status for '{SERVICE_NAME}' matches: '{db_status}'")

    # 2. Compare Last Heartbeat (allowing for a small time difference)
    # Assuming ISO 8601 format (e.g., "2023-10-27T10:00:00Z" or "2023-10-27T10:00:00.123456")
    try:
        # Handle potential timezone info ('Z' for UTC) and microseconds
        # fromisoformat handles 'Z' if it's replaced with '+00:00'
        db_dt = datetime.fromisoformat(db_heartbeat_str.replace('Z', '+00:00'))
        api_dt = datetime.fromisoformat(api_heartbeat_str.replace('Z', '+00:00'))
    except ValueError as e:
        print(f"ERROR: Failed to parse heartbeat timestamp into datetime object: {e}")
        print(f"  DB Heartbeat string: '{db_heartbeat_str}'")
        print(f"  API Heartbeat string: '{api_heartbeat_str}'")
        # Fallback to direct string comparison if parsing fails, but this is less robust
        if db_heartbeat_str != api_heartbeat_str:
            print(f"FAIL: Last Heartbeat string mismatch for '{SERVICE_NAME}' (parsing failed, strings differ).")
            return False
        else:
            print(f"WARN: Heartbeat parsing failed, but strings match. Proceeding with caution.")
            return True # Strings match, so it's a pass for heartbeat
    
    if abs(db_dt - api_dt) > HEARTBEAT_TIME_DELTA_THRESHOLD:
        print(f"FAIL: Last Heartbeat timestamp mismatch for '{SERVICE_NAME}' (difference > {HEARTBEAT_TIME_DELTA_THRESHOLD}).")
        print(f"  DB Heartbeat: {db_dt.isoformat()}")
        print(f"  API Heartbeat: {api_dt.isoformat()}")
        print(f"  Difference: {abs(db_dt - api_dt)}")
        return False
    
    print(f"  Last Heartbeat for '{SERVICE_NAME}' matches within {HEARTBEAT_TIME_DELTA_THRESHOLD}.")
    return True

# --- Main Execution ---
if __name__ == "__main__":
    print(f"--- Verifying '{SERVICE_NAME}' Health Dashboard Integration ---")

    db_health_data = get_db_service_health(SERVICE_NAME)
    api_health_data = get_dashboard_api_service_health(SERVICE_NAME)

    if compare_health_data(db_health_data, api_health_data):
        print(f"\nPASS: {SERVICE_NAME} health correctly integrated into dashboard API")
        sys.exit(0)
    else:
        print(f"\nFAIL: {SERVICE_NAME} health mismatch")
        sys.exit(1)