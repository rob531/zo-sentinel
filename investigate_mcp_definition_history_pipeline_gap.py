import requests
import json
import datetime
import sys

# --- Configuration ---
# Placeholder URLs for the database and log API endpoints.
# In a real system, these would be actual API endpoints.
# The DB_API_URL is assumed to handle all table queries via POST requests.
# The LOG_API_URL is assumed to handle log retrieval via POST requests.
DB_API_URL = "http://localhost:8000/api/db_query"  # Replace with your actual DB API endpoint
LOG_API_URL = "http://localhost:8000/api/read_logs" # Replace with your actual Log API endpoint

# --- Helper Functions for API Interaction ---

def _query_api(url, payload, description="API call"):
    """
    Helper function to make POST requests to an API endpoint.
    """
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        return response.json()
    except requests.exceptions.Timeout:
        print(f"ERROR: {description} timed out after 30 seconds.", file=sys.stderr)
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Could not connect to {url} for {description}. Is the service running?", file=sys.stderr)
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: HTTP error during {description}: {e.response.status_code} - {e.response.text}", file=sys.stderr)
    except requests.exceptions.RequestException as e:
        print(f"ERROR: An unexpected request error occurred during {description}: {e}", file=sys.stderr)
    except json.JSONDecodeError:
        print(f"ERROR: Failed to decode JSON response from {url} for {description}. Response: {response.text}", file=sys.stderr)
    return None

def _query_db(table_name, action="select", params=None, description=None):
    """
    Queries the database API for a specific table.
    Assumes the DB_API_URL expects a JSON payload like:
    {"table": "table_name", "action": "select", "params": {...}}
    """
    payload = {"table": table_name, "action": action, "params": params if params is not None else {}}
    if description is None:
        description = f"querying table '{table_name}' with action '{action}'"
    return _query_api(DB_API_URL, payload, description)

def _read_logs(service_name, time_range_seconds=3600, description=None):
    """
    Reads logs for a specific service from the log API.
    Assumes the LOG_API_URL expects a JSON payload like:
    {"service_name": "service_name", "time_range_seconds": 3600}
    """
    payload = {"service_name": service_name, "time_range_seconds": time_range_seconds}
    if description is None:
        description = f"reading logs for service '{service_name}'"
    return _query_api(LOG_API_URL, payload, description)

# --- Main Investigation Logic ---

def run():
    """
    Executes the investigation pipeline and prints a detailed report.
    """
    report = []
    report.append("--- MCP Definition History Pipeline Investigation Report ---")
    report.append(f"Report generated on: {datetime.datetime.now().isoformat()}")
    report.append(f"DB API Endpoint: {DB_API_URL}")
    report.append(f"Log API Endpoint: {LOG_API_URL}\n")

    # 1. Check `mcp_definition_history` table status
    report.append("### 1. `mcp_definition_history` Table Status ###")
    history_count_result = _query_db(
        "mcp_definition_history",
        action="count",
        params={}
    )
    if history_count_result and 'count' in history_count_result:
        count = history_count_result['count']
        report.append(f"Current record count in `mcp_definition_history`: {count}")
        if count == 0:
            report.append("STATUS: `mcp_definition_history` is EMPTY. This confirms the gap.")
        else:
            report.append("STATUS: `mcp_definition_history` contains data. The issue might be partial or resolved.")
            # Optionally, fetch some recent entries if not empty to see if it's actively populating
            recent_history = _query_db(
                "mcp_definition_history",
                action="select",
                params={"order_by": "timestamp DESC", "limit": 5}
            )
            if recent_history and 'data' in recent_history:
                report.append("Recent entries (last 5):")
                for entry in recent_history['data']:
                    report.append(f"  - {entry}")
            else:
                report.append("  (Could not fetch recent entries.)")
    else:
        report.append("ERROR: Failed to retrieve count for `mcp_definition_history`. Cannot confirm emptiness.")
    report.append("")

    # 2. Analyze `mcp_submissions` for upstream data
    report.append("### 2. `mcp_submissions` Analysis (Upstream Data Source) ###")
    submissions_total_count_result = _query_db(
        "mcp_submissions",
        action="count",
        params={}
    )
    if submissions_total_count_result and 'count' in submissions_total_count_result:
        total_submissions = submissions_total_count_result['count']
        report.append(f"Total records in `mcp_submissions`: {total_submissions}")
        if total_submissions == 0:
            report.append("WARNING: `mcp_submissions` table is EMPTY. No upstream data to process.")
        else:
            # Check for recent submissions (e.g., last 7 days)
            seven_days_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()
            recent_submissions_count_result = _query_db(
                "mcp_submissions",
                action="count",
                params={"where": {"submission_timestamp": {"gt": seven_days_ago}}}
            )
            if recent_submissions_count_result and 'count' in recent_submissions_count_result:
                recent_submissions = recent_submissions_count_result['count']
                report.append(f"Submissions in the last 7 days: {recent_submissions}")
                if recent_submissions == 0:
                    report.append("WARNING: No recent submissions in `mcp_submissions`. This could be why history is not updating.")
                else:
                    report.append("STATUS: Recent submissions are present in `mcp_submissions`. Upstream data exists.")
            else:
                report.append("ERROR: Failed to retrieve recent submissions count.")
    else:
        report.append("ERROR: Failed to retrieve total count for `mcp_submissions`.")
    report.append("")

    # 3. Analyze `mcp_server_registry` for active servers
    report.append("### 3. `mcp_server_registry` Analysis (Submission Sources) ###")
    server_count_result = _query_db(
        "mcp_server_registry",
        action="count",
        params={}
    )
    if server_count_result and 'count' in server_count_result:
        total_servers = server_count_result['count']
        report.append(f"Total registered servers in `mcp_server_registry`: {total_servers}")
        if total_servers == 0:
            report.append("WARNING: `mcp_server_registry` is EMPTY. No servers are registered to make submissions.")
        else:
            active_servers_count_result = _query_db(
                "mcp_server_registry",
                action="count",
                params={"where": {"status": "active"}} # Assuming a 'status' field
            )
            if active_servers_count_result and 'count' in active_servers_count_result:
                active_servers = active_servers_count_result['count']
                report.append(f"Active servers: {active_servers}")
                if active_servers == 0:
                    report.append("WARNING: No active servers found. Submissions might not be occurring.")
                else:
                    report.append("STATUS: Active servers are registered. Potential sources for submissions exist.")
            else:
                report.append("ERROR: Failed to retrieve active server count.")
    else:
        report.append("ERROR: Failed to retrieve total count for `mcp_server_registry`.")
    report.append("")

    # 4. Verify Daemon Health via `service_health`
    report.append("### 4. Daemon Health Check (`service_health`) ###")

    daemons_to_check = [
        "mcp_definition_history_populator_daemon",
        "mcp_definition_history_backfill_daemon"
    ]

    for daemon_name in daemons_to_check:
        report.append(f"--- Daemon: {daemon_name} ---")
        daemon_health_result = _query_db(
            "service_health",
            action="select",
            params={"where": {"service_name": daemon_name}, "limit": 1, "order_by": "last_heartbeat DESC"}
        )
        if daemon_health_result and 'data' in daemon_health_result and daemon_health_result['data']:
            health_data = daemon_health_result['data'][0]
            report.append(f"  Status: {health_data.get('status', 'N/A')}")
            report.append(f"  Last Heartbeat: {health_data.get('last_heartbeat', 'N/A')}")
            report.append(f"  Error Count (last cycle/period): {health_data.get('error_count', 'N/A')}")
            report.append(f"  Last Error Message: {health_data.get('last_error_message', 'N/A')}")

            if health_data.get('status') != 'running':
                report.append(f"  CRITICAL: Daemon '{daemon_name}' is NOT running or has an unhealthy status!")
            elif health_data.get('error_count', 0) > 0:
                report.append(f"  WARNING: Daemon '{daemon_name}' reported errors in its last cycle.")
            else:
                report.append(f"  STATUS: Daemon '{daemon_name}' appears to be running and healthy.")
        else:
            report.append(f"  ERROR: No health data found for daemon '{daemon_name}'. It might not be registered or running.")
        report.append("")

    # 5. Log Analysis for Daemons
    report.append("### 5. Daemon Log Analysis ###")
    log_time_range_seconds = 3600 * 24 # Check logs for the last 24 hours

    for daemon_name in daemons_to_check:
        report.append(f"--- Logs for: {daemon_name} (last {log_time_range_seconds // 3600} hours) ---")
        daemon_logs = _read_logs(daemon_name, time_range_seconds=log_time_range_seconds)

        if daemon_logs and 'logs' in daemon_logs:
            error_logs = [log for log in daemon_logs['logs'] if any(keyword in log.upper() for keyword in ["ERROR", "FAILURE", "EXCEPTION", "CRITICAL"])]
            if error_logs:
                report.append(f"  CRITICAL: Found {len(error_logs)} error/failure entries in logs for '{daemon_name}':")
                for i, log_entry in enumerate(error_logs[:5]): # Show top 5 error logs
                    report.append(f"    - {log_entry.strip()}")
                if len(error_logs) > 5:
                    report.append(f"    ... ({len(error_logs) - 5} more error entries)")
            else:
                report.append(f"  STATUS: No critical error/failure entries found in logs for '{daemon_name}'.")
            report.append(f"  Total log entries retrieved: {len(daemon_logs['logs'])}")
        else:
            report.append(f"  ERROR: Failed to retrieve logs for '{daemon_name}'. Log API might be inaccessible or returned no data.")
        report.append("")

    # 6. Summary and Potential Bottlenecks
    report.append("### 6. Summary and Potential Bottlenecks ###")
    if "STATUS: `mcp_definition_history` is EMPTY." in report:
        report.append("The `mcp_definition_history` table is confirmed to be empty.")

    # Synthesize findings
    issues_found = []

    if "WARNING: `mcp_submissions` table is EMPTY." in report:
        issues_found.append("No data in `mcp_submissions`. The pipeline has no input.")
    elif "WARNING: No recent submissions in `mcp_submissions`." in report:
        issues_found.append("No recent submissions. The upstream data flow has stopped or is very slow.")

    if "WARNING: `mcp_server_registry` is EMPTY." in report:
        issues_found.append("No servers registered. No sources for submissions.")
    elif "WARNING: No active servers found." in report:
        issues_found.append("No active servers. Submissions are likely not being made.")

    for daemon_name in daemons_to_check:
        if f"CRITICAL: Daemon '{daemon_name}' is NOT running or has an unhealthy status!" in report:
            issues_found.append(f"Daemon '{daemon_name}' is not running or unhealthy according to `service_health`.")
        if f"WARNING: Daemon '{daemon_name}' reported errors in its last cycle." in report:
            issues_found.append(f"Daemon '{daemon_name}' reported errors in `service_health`.")
        if f"CRITICAL: Found" in "".join(report) and f"for '{daemon_name}'" in "".join(report): # Check for log errors
            issues_found.append(f"Daemon '{daemon_name}' logs contain critical errors/exceptions.")
        if f"ERROR: No health data found for daemon '{daemon_name}'." in report:
            issues_found.append(f"No health data for '{daemon_name}'. It might not be deployed or reporting health.")
        if f"ERROR: Failed to retrieve logs for '{daemon_name}'." in report:
            issues_found.append(f"Could not retrieve logs for '{daemon_name}'. Log access issue or daemon not logging.")

    if issues_found:
        report.append("\nPotential Bottlenecks/Issues Identified:")
        for i, issue in enumerate(issues_found):
            report.append(f"  {i+1}. {issue}")
        report.append("\nRecommended Next Steps:")
        report.append("  - Address any identified upstream data gaps (`mcp_submissions`, `mcp_server_registry`).")
        report.append("  - Investigate and restart any unhealthy or non-running daemons.")
        report.append("  - Examine full logs for daemons with reported errors for detailed stack traces.")
        report.append("  - Verify network connectivity to DB_API_URL and LOG_API_URL.")
        report.append("  - Check daemon configuration for correct table/queue names and permissions.")
    else:
        report.append("No critical issues were immediately identified by this script, assuming API endpoints are correctly configured and accessible.")
        report.append("If the table is still empty, consider:")
        report.append("  - Deeper dive into daemon internal metrics (if available).")
        report.append("  - Manual inspection of daemon processes and their environment.")
        report.append("  - Verifying the data transformation logic within the populator daemon.")

    report.append("\n--- End of Report ---")

    # Print the full report to stdout
    for line in report:
        print(line)

if __name__ == "__main__":
    run()
    sys.exit(0) # Ensure the script exits with status 0 upon completion