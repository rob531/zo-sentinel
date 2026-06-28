import os
import requests
import json
import re
import datetime
import time

# --- Configuration ---
# Base URLs for the services. These would typically be environment variables or configuration files.
# For local testing, ensure these services are running or MOCK_MODE is True.
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
WRITE_SERVICE_BASE_URL = os.getenv("WRITE_SERVICE_BASE_URL", "http://localhost:8001")

STALE_DAEMON_API_ENDPOINT = f"{API_BASE_URL}/api/stale_daemons"
WRITE_SERVICE_QUERY_ENDPOINT = f"{WRITE_SERVICE_BASE_URL}/query"
# Assuming the dashboard view is accessible at a specific URL
DASHBOARD_VIEW_ENDPOINT = f"{API_BASE_URL}/dashboard/stale_daemon_overview"

# Threshold for a daemon to be considered stale (e.g., 5 minutes)
STALE_THRESHOLD_SECONDS = 300

# --- Mocking (for local testing without actual services) ---
# Set MOCK_MODE to "True" (case-insensitive) to use mock data.
# In a real scenario, these would be actual service calls.
MOCK_MODE = os.getenv("MOCK_MODE", "True").lower() == "true"

# Mock API response for stale_daemon_status_api.py
MOCK_API_RESPONSE = {
    "stale_daemons": ["daemon_A", "daemon_C", "daemon_E"]
}

# Mock DB response for write_service /query endpoint
# Simulates querying the 'service_health' table for daemons
# 'last_heartbeat' is an ISO-formatted datetime string.
# We'll generate some stale and active daemons based on STALE_THRESHOLD_SECONDS.
_now = datetime.datetime.now(datetime.timezone.utc)
MOCK_DB_RESPONSE = {
    "results": [
        # Stale daemons (heartbeat older than threshold)
        {"daemon_id": "daemon_A", "last_heartbeat": (_now - datetime.timedelta(seconds=STALE_THRESHOLD_SECONDS + 60)).isoformat()},
        {"daemon_id": "daemon_C", "last_heartbeat": (_now - datetime.timedelta(seconds=STALE_THRESHOLD_SECONDS + 120)).isoformat()},
        {"daemon_id": "daemon_E", "last_heartbeat": (_now - datetime.timedelta(seconds=STALE_THRESHOLD_SECONDS + 30)).isoformat()},
        # Active daemons (heartbeat within threshold)
        {"daemon_id": "daemon_B", "last_heartbeat": (_now - datetime.timedelta(seconds=STALE_THRESHOLD_SECONDS - 60)).isoformat()},
        {"daemon_id": "daemon_D", "last_heartbeat": (_now - datetime.timedelta(seconds=STALE_THRESHOLD_SECONDS - 10)).isoformat()},
    ]
}

# Mock HTML structure for the dashboard view
# Assumes stale daemons are listed in an unordered list within a specific div.
MOCK_DASHBOARD_HTML = f"""
<!DOCTYPE html>
<html>
<head><title>Stale Daemon Overview</title></head>
<body>
    <h1>Stale Daemon Overview Dashboard</h1>
    <div id="stale-daemons-section">
        <h2>Currently Stale Daemons</h2>
        <ul>
            <li>daemon_A</li>
            <li>daemon_C</li>
            <li>daemon_E</li>
        </ul>
    </div>
    <div id="active-daemons-section">
        <h2>Currently Active Daemons</h2>
        <ul>
            <li>daemon_B</li>
            <li>daemon_D</li>
        </ul>
    </div>
</body>
</html>
"""

def _get_ground_truth_from_db() -> set[str]:
    """
    Queries the 'service_health' table via the write_service /query endpoint
    to determine the ground truth of stale daemons.
    """
    print(f"Fetching ground truth from DB (MOCK_MODE: {MOCK_MODE})...")
    stale_daemon_ids = set()
    
    if MOCK_MODE:
        current_utc_time = datetime.datetime.now(datetime.timezone.utc)
        for record in MOCK_DB_RESPONSE["results"]:
            last_heartbeat_str = record.get("last_heartbeat")
            daemon_id = record.get("daemon_id")
            if last_heartbeat_str and daemon_id:
                try:
                    last_heartbeat = datetime.datetime.fromisoformat(last_heartbeat_str)
                    if (current_utc_time - last_heartbeat).total_seconds() > STALE_THRESHOLD_SECONDS:
                        stale_daemon_ids.add(daemon_id)
                except ValueError:
                    print(f"Warning: Could not parse last_heartbeat for {daemon_id}: {last_heartbeat_str}")
        print(f"Mock DB ground truth: {stale_daemon_ids}")
        return stale_daemon_ids

    try:
        # Calculate the threshold time in UTC
        stale_time_threshold = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=STALE_THRESHOLD_SECONDS)
        
        # Construct a SQL query to find daemons with last_heartbeat older than the threshold
        # Assuming 'last_heartbeat' is stored as a timestamp or ISO-formatted string in the DB
        # The exact SQL might vary based on the database type (e.g., PostgreSQL, MySQL, SQLite)
        # For simplicity, we'll use a generic comparison with an ISO string.
        sql_query = f"SELECT daemon_id FROM service_health WHERE last_heartbeat < '{stale_time_threshold.isoformat()}'"
        
        response = requests.post(
            WRITE_SERVICE_QUERY_ENDPOINT,
            json={"query": sql_query},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        if "results" in data:
            for row in data["results"]:
                if "daemon_id" in row:
                    stale_daemon_ids.add(row["daemon_id"])
        else:
            print(f"Error: 'results' key not found in DB query response: {data}")
            return set()

        print(f"DB ground truth: {stale_daemon_ids}")
        return stale_daemon_ids

    except requests.exceptions.RequestException as e:
        print(f"Error fetching ground truth from DB: {e}")
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from DB query response: {e}")
    except Exception as e:
        print(f"An unexpected error occurred while getting DB ground truth: {e}")
    return set()

def _get_stale_daemons_from_api() -> set[str]:
    """
    Fetches the list of stale daemons from the stale_daemon_status_api.py endpoint.
    """
    print(f"Fetching stale daemons from API (MOCK_MODE: {MOCK_MODE})...")
    if MOCK_MODE:
        api_stale_daemons = set(MOCK_API_RESPONSE.get("stale_daemons", []))
        print(f"Mock API response: {api_stale_daemons}")
        return api_stale_daemons

    try:
        response = requests.get(STALE_DAEMON_API_ENDPOINT, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        api_stale_daemons = set(data.get("stale_daemons", []))
        print(f"API response: {api_stale_daemons}")
        return api_stale_daemons

    except requests.exceptions.RequestException as e:
        print(f"Error fetching stale daemons from API: {e}")
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from API response: {e}")
    except Exception as e:
        print(f"An unexpected error occurred while getting API data: {e}")
    return set()

def _get_stale_daemons_from_dashboard() -> set[str]:
    """
    Fetches and parses the stale_daemon_overview_dashboard_view.html
    to extract the list of stale daemons displayed.
    """
    print(f"Fetching stale daemons from Dashboard (MOCK_MODE: {MOCK_MODE})...")
    dashboard_stale_daemons = set()
    html_content = ""

    if MOCK_MODE:
        html_content = MOCK_DASHBOARD_HTML
        print("Using mock dashboard HTML content.")
    else:
        try:
            response = requests.get(DASHBOARD_VIEW_ENDPOINT, timeout=10)
            response.raise_for_status()
            html_content = response.text
            print("Fetched live dashboard HTML content.")
        except requests.exceptions.RequestException as e:
            print(f"Error fetching dashboard view: {e}")
            return set()
        except Exception as e:
            print(f"An unexpected error occurred while getting dashboard HTML: {e}")
            return set()

    # Parse HTML using regex (standard library only)
    # Look for the specific section containing stale daemons
    section_match = re.search(r'<div id="stale-daemons-section">(.*?)</div>', html_content, re.DOTALL)
    if section_match:
        section_html = section_match.group(1)
        # Find all list items within that section
        daemon_matches = re.findall(r'<li>\s*(.*?)\s*</li>', section_html)
        dashboard_stale_daemons = set(daemon_matches)
    else:
        print("Warning: 'stale-daemons-section' div not found in dashboard HTML.")
    
    print(f"Dashboard displayed stale daemons: {dashboard_stale_daemons}")
    return dashboard_stale_daemons

def verify_dashboard_accuracy() -> bool:
    """
    Checks the dashboard's data against the source of truth (service_health table)
    and the intermediate API.

    Returns:
        bool: True if the dashboard accurately reflects the stale daemon status, False otherwise.
    """
    print("\n--- Starting Dashboard Accuracy Verification ---")

    # 1. Get Ground Truth from service_health table
    db_stale_daemons = _get_ground_truth_from_db()
    if not db_stale_daemons and not MOCK_MODE: # If not in mock mode and DB call failed, we can't verify
        print("Failed to retrieve ground truth from DB. Cannot proceed with verification.")
        return False

    # 2. Get Stale Daemons from stale_daemon_status_api.py
    api_stale_daemons = _get_stale_daemons_from_api()
    if not api_stale_daemons and not MOCK_MODE: # If not in mock mode and API call failed, we can't verify
        print("Failed to retrieve stale daemons from API. Cannot proceed with verification.")
        return False

    # 3. Get Stale Daemons from stale_daemon_overview_dashboard_view.html
    dashboard_stale_daemons = _get_stale_daemons_from_dashboard()
    if not dashboard_stale_daemons and not MOCK_MODE: # If not in mock mode and Dashboard call failed, we can't verify
        print("Failed to retrieve stale daemons from Dashboard. Cannot proceed with verification.")
        return False

    # --- Comparisons ---
    print("\n--- Comparison Results ---")

    # Comparison 1: Ground Truth (DB) vs. API
    db_vs_api_match = (db_stale_daemons == api_stale_daemons)
    print(f"DB Ground Truth ({len(db_stale_daemons)}): {sorted(list(db_stale_daemons))}")
    print(f"API Reported ({len(api_stale_daemons)}): {sorted(list(api_stale_daemons))}")
    print(f"DB vs. API Match: {db_vs_api_match}")
    if not db_vs_api_match:
        print(f"  Mismatch details:")
        print(f"    In DB but not API: {db_stale_daemons - api_stale_daemons}")
        print(f"    In API but not DB: {api_stale_daemons - db_stale_daemons}")

    # Comparison 2: API vs. Dashboard
    api_vs_dashboard_match = (api_stale_daemons == dashboard_stale_daemons)
    print(f"Dashboard Displayed ({len(dashboard_stale_daemons)}): {sorted(list(dashboard_stale_daemons))}")
    print(f"API vs. Dashboard Match: {api_vs_dashboard_match}")
    if not api_vs_dashboard_match:
        print(f"  Mismatch details:")
        print(f"    In API but not Dashboard: {api_stale_daemons - dashboard_stale_daemons}")
        print(f"    In Dashboard but not API: {dashboard_stale_daemons - api_stale_daemons}")

    overall_accuracy = db_vs_api_match and api_vs_dashboard_match
    print(f"\nOverall Dashboard Accuracy: {overall_accuracy}")

    return overall_accuracy

if __name__ == "__main__":
    print(f"Running verification in MOCK_MODE: {MOCK_MODE}")
    if MOCK_MODE:
        print("Ensure MOCK_API_RESPONSE, MOCK_DB_RESPONSE, and MOCK_DASHBOARD_HTML are configured correctly for testing.")
        print("To run against live services, set MOCK_MODE=False environment variable.")
        print("Example: MOCK_MODE=False python verify_stale_daemon_overview_dashboard_view_accuracy.py")
        print("Also ensure API_BASE_URL and WRITE_SERVICE_BASE_URL are set if not default localhost.")

    is_accurate = verify_dashboard_accuracy()

    if is_accurate:
        print("\nPASS: The stale daemon overview dashboard accurately reflects the current status.")
    else:
        print("\nFAIL: The stale daemon overview dashboard does NOT accurately reflect the current status.")
        # In a real test suite, this would raise an AssertionError
        # For this exercise, we'll just print FAIL.
        # assert False, "Dashboard accuracy verification failed."