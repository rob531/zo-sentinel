import requests
import json
import datetime
import os
import re
import sqlite3
import sys
from io import StringIO
from contextlib import redirect_stdout

# --- Configuration ---
# API endpoint for mcp_search_results_api.py.
# For demonstration, we'll use httpbin.org to simulate success and various failures.
# Replace with your actual API endpoint.
API_ENDPOINT_SUCCESS = "https://httpbin.org/get?query=test&limit=10"
API_ENDPOINT_FAILURE_500 = "https://httpbin.org/status/500"
API_ENDPOINT_FAILURE_404 = "https://httpbin.org/status/404"
API_ENDPOINT_FAILURE_TIMEOUT = "https://httpbin.org/delay/5" # Simulate timeout if timeout is set low
API_ENDPOINT_INVALID_JSON = "https://httpbin.org/html" # Returns HTML, not JSON

# Log file path for mcp_search_results_api.py.
# This will be a simulated file for the purpose of this diagnostic script.
LOG_FILE_PATH = "mcp_search_results_api.log"

# Database file path.
# This will be a simulated SQLite database.
DB_FILE_PATH = "mcp_search_results.db"

# Timeout for API requests in seconds
API_TIMEOUT = 3

# --- Helper Functions for Simulation ---

def _simulate_log_file(path, content_type="success"):
    """
    Creates a dummy log file with different content based on content_type.
    """
    log_content = []
    now = datetime.datetime.now()
    
    if content_type == "success":
        log_content.append(f"{now.isoformat()} INFO mcp_search_results_api: API started successfully.")
        log_content.append(f"{now.isoformat()} INFO mcp_search_results_api: Request received: /search?query=test")
        log_content.append(f"{now.isoformat()} INFO mcp_search_results_api: Search query executed successfully.")
        log_content.append(f"{now.isoformat()} INFO mcp_search_results_api: Response sent with 200 OK.")
    elif content_type == "api_error":
        log_content.append(f"{now.isoformat()} INFO mcp_search_results_api: API started successfully.")
        log_content.append(f"{now.isoformat()} INFO mcp_search_results_api: Request received: /search?query=error_test")
        log_content.append(f"{now.isoformat()} ERROR mcp_search_results_api: External API call failed with status 500.")
        log_content.append(f"{now.isoformat()} ERROR mcp_search_results_api: Failed to process search request for 'error_test'. Exception: requests.exceptions.HTTPError: 500 Server Error")
    elif content_type == "db_error":
        log_content.append(f"{now.isoformat()} INFO mcp_search_results_api: API started successfully.")
        log_content.append(f"{now.isoformat()} INFO mcp_search_results_api: Request received: /search?query=db_issue")
        log_content.append(f"{now.isoformat()} ERROR mcp_search_results_api: Database connection failed. Exception: sqlite3.OperationalError: unable to open database file")
        log_content.append(f"{now.isoformat()} ERROR mcp_search_results_api: Failed to retrieve search history. Exception: sqlite3.ProgrammingError: Cannot operate on a closed database.")
    elif content_type == "timeout":
        log_content.append(f"{now.isoformat()} INFO mcp_search_results_api: API started successfully.")
        log_content.append(f"{now.isoformat()} INFO mcp_search_results_api: Request received: /search?query=slow_query")
        log_content.append(f"{now.isoformat()} WARNING mcp_search_results_api: Search query took longer than expected.")
        log_content.append(f"{now.isoformat()} ERROR mcp_search_results_api: Request timed out after 3 seconds. Exception: requests.exceptions.Timeout")
    elif content_type == "no_logs":
        # Create an empty log file
        pass
    
    with open(path, "w") as f:
        f.write("\n".join(log_content))

def _simulate_database(path, content_type="success"):
    """
    Creates a dummy SQLite database with different content/state.
    """
    conn = None
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS search_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                status TEXT NOT NULL
            )
        """)
        
        if content_type == "success":
            cursor.execute("INSERT INTO search_requests (query, timestamp, status) VALUES (?, ?, ?)",
                           ("test query 1", datetime.datetime.now().isoformat(), "SUCCESS"))
            cursor.execute("INSERT INTO search_requests (query, timestamp, status) VALUES (?, ?, ?)",
                           ("test query 2", (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat(), "SUCCESS"))
        elif content_type == "empty":
            # Table exists but is empty
            pass
        elif content_type == "corrupt":
            # Simulate a corrupt DB by closing it immediately without committing,
            # or by writing garbage (though SQLite handles this robustly).
            # For this simulation, we'll just ensure it's created but might not be fully functional
            # or we'll simulate a connection error in the diagnostic.
            # A more direct simulation of corruption is hard without low-level file manipulation.
            # We'll rely on the diagnostic to catch connection issues.
            pass
            
        conn.commit()
    except sqlite3.Error as e:
        print(f"Simulated DB error during setup: {e}")
    finally:
        if conn:
            conn.close()

def _cleanup_simulated_files():
    """Removes simulated log and DB files."""
    if os.path.exists(LOG_FILE_PATH):
        os.remove(LOG_FILE_PATH)
    if os.path.exists(DB_FILE_PATH):
        os.remove(DB_FILE_PATH)

# --- Diagnostic Functions ---

def _check_api_connectivity(api_url, report):
    """
    Attempts to call the API and reports its status.
    """
    report.append("\n--- API Connectivity Check ---")
    start_time = datetime.datetime.now()
    try:
        response = requests.get(api_url, timeout=API_TIMEOUT)
        end_time = datetime.datetime.now()
        response_time_ms = (end_time - start_time).total_seconds() * 1000
        
        report.append(f"Attempted to call API: {api_url}")
        report.append(f"HTTP Status Code: {response.status_code}")
        report.append(f"Response Time: {response_time_ms:.2f} ms")

        if 200 <= response.status_code < 300:
            report.append("API appears to be reachable and returned a successful status code.")
            try:
                json_response = response.json()
                report.append("Response is valid JSON.")
                report.append(f"Sample response data: {str(json_response)[:100]}...")
            except json.JSONDecodeError:
                report.append("WARNING: API returned a successful status but response is NOT valid JSON.")
                report.append(f"Response content (first 200 chars): {response.text[:200]}...")
            except Exception as e:
                report.append(f"WARNING: Could not parse API response as JSON: {e}")
        elif response.status_code >= 400:
            report.append(f"ERROR: API returned an error status code: {response.status_code}.")
            report.append(f"Possible issues: client error (4xx), server error (5xx).")
            try:
                error_details = response.json()
                report.append(f"Error details from API: {error_details}")
            except json.JSONDecodeError:
                report.append(f"API returned error status but response is not valid JSON. Content: {response.text[:200]}...")
        
    except requests.exceptions.Timeout:
        report.append(f"ERROR: API request timed out after {API_TIMEOUT} seconds.")
        report.append("Possible causes: API is slow, overloaded, or network latency.")
    except requests.exceptions.ConnectionError as e:
        report.append(f"ERROR: Could not connect to the API endpoint.")
        report.append(f"Details: {e}")
        report.append("Possible causes: API service is down, incorrect URL, network issues, firewall.")
    except requests.exceptions.RequestException as e:
        report.append(f"ERROR: An unexpected request error occurred.")
        report.append(f"Details: {e}")
    except Exception as e:
        report.append(f"ERROR: An unhandled exception occurred during API connectivity check: {e}")

def _check_database_health(db_path, report):
    """
    Checks the health and accessibility of the database.
    """
    report.append("\n--- Database Health Check ---")
    conn = None
    try:
        if not os.path.exists(db_path):
            report.append(f"WARNING: Database file '{db_path}' does not exist.")
            report.append("This might indicate a setup issue or a missing database.")
            return

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Try to execute a simple query
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='search_requests';")
        table_exists = cursor.fetchone()
        
        if not table_exists:
            report.append(f"ERROR: Table 'search_requests' does not exist in '{db_path}'.")
            report.append("This indicates a database schema issue or incorrect table name.")
        else:
            report.append(f"Table 'search_requests' found in '{db_path}'.")
            
            # Check for recent data
            cursor.execute("SELECT COUNT(*) FROM search_requests WHERE timestamp > ?", 
                           [(datetime.datetime.now() - datetime.timedelta(hours=1)).isoformat()])
            recent_records = cursor.fetchone()[0]
            
            if recent_records > 0:
                report.append(f"Found {recent_records} search records in the last hour.")
                report.append("Database appears to be actively recording data.")
            else:
                report.append("WARNING: No search records found in the last hour.")
                report.append("This could indicate low activity or a data insertion issue.")
            
            # Check for any errors in recent records (if 'status' column is used)
            cursor.execute("SELECT COUNT(*) FROM search_requests WHERE status = 'ERROR' AND timestamp > ?",
                           [(datetime.datetime.now() - datetime.timedelta(hours=1)).isoformat()])
            error_records = cursor.fetchone()[0]
            if error_records > 0:
                report.append(f"WARNING: Found {error_records} 'ERROR' status records in the last hour.")
                report.append("Investigate these records for specific API failures.")

    except sqlite3.OperationalError as e:
        report.append(f"ERROR: Could not connect to or operate on database '{db_path}'.")
        report.append(f"Details: {e}")
        report.append("Possible causes: Database file is corrupt, locked, permissions issue, or path is incorrect.")
    except Exception as e:
        report.append(f"ERROR: An unhandled exception occurred during database check: {e}")
    finally:
        if conn:
            conn.close()

def _parse_logs(log_file_path, report):
    """
    Parses the log file for errors and warnings.
    """
    report.append("\n--- Log Analysis ---")
    if not os.path.exists(log_file_path):
        report.append(f"WARNING: Log file '{log_file_path}' not found.")
        report.append("Cannot perform log analysis. Ensure logging is configured correctly.")
        return

    error_pattern = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}) (ERROR|WARNING) (.*)")
    errors_found = []
    warnings_found = []
    
    try:
        with open(log_file_path, 'r') as f:
            for line in f:
                match = error_pattern.search(line)
                if match:
                    timestamp_str, level, message = match.groups()
                    log_entry = f"[{timestamp_str}] {level}: {message.strip()}"
                    if level == "ERROR":
                        errors_found.append(log_entry)
                    elif level == "WARNING":
                        warnings_found.append(log_entry)
        
        if errors_found:
            report.append(f"Found {len(errors_found)} ERROR entries in logs:")
            for error in errors_found:
                report.append(f"  - {error}")
        else:
            report.append("No ERROR entries found in logs.")
            
        if warnings_found:
            report.append(f"Found {len(warnings_found)} WARNING entries in logs:")
            for warning in warnings_found:
                report.append(f"  - {warning}")
        else:
            report.append("No WARNING entries found in logs.")

        if not errors_found and not warnings_found:
            report.append("Logs appear clean (no ERROR or WARNING entries).")

    except IOError as e:
        report.append(f"ERROR: Could not read log file '{log_file_path}'.")
        report.append(f"Details: {e}")
        report.append("Possible causes: Permissions issue, file is locked, or path is incorrect.")
    except Exception as e:
        report.append(f"ERROR: An unhandled exception occurred during log parsing: {e}")

def run(api_endpoint_override=None):
    """
    Runs the diagnostic checks for mcp_search_results_api.py failures.
    Prints a diagnostic report to the console.
    """
    diagnostic_report = []
    
    diagnostic_report.append("--- Starting mcp_search_results_api.py Diagnostic ---")
    diagnostic_report.append(f"Timestamp: {datetime.datetime.now().isoformat()}")
    diagnostic_report.append(f"API Endpoint: {api_endpoint_override if api_endpoint_override else API_ENDPOINT_SUCCESS}")
    diagnostic_report.append(f"Log File: {LOG_FILE_PATH}")
    diagnostic_report.append(f"Database File: {DB_FILE_PATH}")

    # 1. API Connectivity Check
    _check_api_connectivity(api_endpoint_override if api_endpoint_override else API_ENDPOINT_SUCCESS, diagnostic_report)

    # 2. Database Health Check
    _check_database_health(DB_FILE_PATH, diagnostic_report)

    # 3. Log Analysis
    _parse_logs(LOG_FILE_PATH, diagnostic_report)

    diagnostic_report.append("\n--- Diagnostic Summary ---")
    
    # Synthesize findings
    api_issues = any("ERROR: API" in line for line in diagnostic_report)
    db_issues = any("ERROR: Could not connect to or operate on database" in line or "ERROR: Table 'search_requests' does not exist" in line for line in diagnostic_report)
    log_errors = any("ERROR entries found in logs" in line for line in diagnostic_report)
    
    if api_issues:
        diagnostic_report.append("CRITICAL: API connectivity issues detected. Check network, API service status, and endpoint configuration.")
    if db_issues:
        diagnostic_report.append("CRITICAL: Database issues detected. Check database file integrity, permissions, and schema.")
    if log_errors:
        diagnostic_report.append("CRITICAL: Error entries found in application logs. Review logs for specific exceptions and stack traces.")
    
    if not api_issues and not db_issues and not log_errors:
        diagnostic_report.append("INFO: No critical issues detected by the diagnostic script. API, DB, and logs appear healthy.")
        diagnostic_report.append("If problems persist, consider deeper application-level debugging or increased logging verbosity.")
    else:
        diagnostic_report.append("ACTION REQUIRED: Review the detailed report above for specific errors and warnings.")

    print("\n".join(diagnostic_report))
    return "\n".join(diagnostic_report) # Return for testing purposes

# --- Main Block for Acceptance Testing ---
if __name__ == "__main__":
    print("--- Running Diagnostic Scenarios ---")

    # Scenario 1: All systems healthy
    print("\n\n--- Scenario 1: All Systems Healthy ---")
    _cleanup_simulated_files()
    _simulate_log_file(LOG_FILE_PATH, "success")
    _simulate_database(DB_FILE_PATH, "success")
    
    captured_output = StringIO()
    with redirect_stdout(captured_output):
        report_s1 = run(API_ENDPOINT_SUCCESS)
    output_s1 = captured_output.getvalue()
    print(output_s1)
    assert "API appears to be reachable and returned a successful status code." in output_s1
    assert "Table 'search_requests' found" in output_s1
    assert "No ERROR entries found in logs." in output_s1
    assert "No critical issues detected by the diagnostic script." in output_s1
    print("Scenario 1 PASSED: Healthy diagnosis confirmed.")

    # Scenario 2: API Down (Connection Error)
    print("\n\n--- Scenario 2: API Down (Connection Error) ---")
    _cleanup_simulated_files()
    _simulate_log_file(LOG_FILE_PATH, "api_error") # Log reflects API error
    _simulate_database(DB_FILE_PATH, "success")
    
    captured_output = StringIO()
    with redirect_stdout(captured_output):
        # Use a non-existent local URL to simulate connection error
        report_s2 = run("http://localhost:9999/nonexistent_api") 
    output_s2 = captured_output.getvalue()
    print(output_s2)
    assert "ERROR: Could not connect to the API endpoint." in output_s2
    assert "CRITICAL: API connectivity issues detected." in output_s2
    assert "ERROR entries found in logs:" in output_s2 # From simulated log
    print("Scenario 2 PASSED: API connection error diagnosis confirmed.")

    # Scenario 3: API Returns 500 Server Error
    print("\n\n--- Scenario 3: API Returns 500 Server Error ---")
    _cleanup_simulated_files()
    _simulate_log_file(LOG_FILE_PATH, "api_error")
    _simulate_database(DB_FILE_PATH, "success")
    
    captured_output = StringIO()
    with redirect_stdout(captured_output):
        report_s3 = run(API_ENDPOINT_FAILURE_500)
    output_s3 = captured_output.getvalue()
    print(output_s3)
    assert "HTTP Status Code: 500" in output_s3
    assert "ERROR: API returned an error status code: 500." in output_s3
    assert "CRITICAL: API connectivity issues detected." in output_s3
    assert "ERROR entries found in logs:" in output_s3
    print("Scenario 3 PASSED: API 500 error diagnosis confirmed.")

    # Scenario 4: Database Corruption/Unreachable
    print("\n\n--- Scenario 4: Database Corruption/Unreachable ---")
    _cleanup_simulated_files()
    _simulate_log_file(LOG_FILE_PATH, "db_error")
    # Do NOT simulate database successfully, or delete it after creation to simulate corruption
    _simulate_database(DB_FILE_PATH, "success") # Create it first
    os.remove(DB_FILE_PATH) # Then remove it to simulate "not found" or "corrupt" on connect

    captured_output = StringIO()
    with redirect_stdout(captured_output):
        report_s4 = run(API_ENDPOINT_SUCCESS)
    output_s4 = captured_output.getvalue()
    print(output_s4)
    assert "WARNING: Database file 'mcp_search_results.db' does not exist." in output_s4
    assert "CRITICAL: Database issues detected." in output_s4
    assert "ERROR entries found in logs:" in output_s4 # From simulated log
    print("Scenario 4 PASSED: Database issue diagnosis confirmed.")

    # Scenario 5: API Timeout
    print("\n\n--- Scenario 5: API Timeout ---")
    _cleanup_simulated_files()
    _simulate_log_file(LOG_FILE_PATH, "timeout")
    _simulate_database(DB_FILE_PATH, "success")
    
    captured_output = StringIO()
    with redirect_stdout(captured_output):
        # Use httpbin.org/delay/X where X > API_TIMEOUT
        report_s5 = run(API_ENDPOINT_FAILURE_TIMEOUT) 
    output_s5 = captured_output.getvalue()
    print(output_s5)
    assert f"ERROR: API request timed out after {API_TIMEOUT} seconds." in output_s5
    assert "CRITICAL: API connectivity issues detected." in output_s5
    assert "ERROR entries found in logs:" in output_s5 # From simulated log
    print("Scenario 5 PASSED: API timeout diagnosis confirmed.")

    # Scenario 6: Log file missing
    print("\n\n--- Scenario 6: Log File Missing ---")
    _cleanup_simulated_files()
    # Do not create log file
    _simulate_database(DB_FILE_PATH, "success")
    
    captured_output = StringIO()
    with redirect_stdout(captured_output):
        report_s6 = run(API_ENDPOINT_SUCCESS)
    output_s6 = captured_output.getvalue()
    print(output_s6)
    assert f"WARNING: Log file '{LOG_FILE_PATH}' not found." in output_s6
    assert "No critical issues detected by the diagnostic script." in output_s6 # Only a warning, not critical
    print("Scenario 6 PASSED: Missing log file warning confirmed.")

    _cleanup_simulated_files()
    print("\n--- All Diagnostic Scenarios Completed ---")