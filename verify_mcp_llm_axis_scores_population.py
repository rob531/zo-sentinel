import requests
import os
import datetime
import sys

# --- Configuration ---
# The URL of the write_service. Defaults to a common local development address.
# It's recommended to set this via an environment variable in production.
WRITE_SERVICE_URL = os.getenv("WRITE_SERVICE_URL", "http://localhost:8000")

# The specific endpoint on the write_service for executing database queries.
# This assumes the service exposes a generic query endpoint.
QUERY_ENDPOINT = "/query_db"

# Minimum number of distinct MCPs expected in the table to consider it "populated".
MIN_MCP_COUNT = 100

# Maximum age (in hours) of the most recently updated record for the data to be considered "fresh".
MAX_DATA_AGE_HOURS = 24

# Timeout for the HTTP request to the write_service in seconds.
REQUEST_TIMEOUT_SECONDS = 30

def verify_mcp_llm_axis_scores_population():
    """
    Connects to the write_service to query the mcp_llm_axis_scores table.
    It asserts that the table contains data for a reasonable number of MCPs,
    and that the data is fresh. Reports on the findings.

    Returns:
        bool: True if all assertions pass, False otherwise.
    """
    print(f"--- Starting Verification of mcp_llm_axis_scores Population ---")
    print(f"Targeting write service at: {WRITE_SERVICE_URL}{QUERY_ENDPOINT}")
    print(f"Expected minimum distinct MCPs: {MIN_MCP_COUNT}")
    print(f"Expected data freshness (max age): {MAX_DATA_AGE_HOURS} hours")

    # SQL query to retrieve aggregate statistics about the mcp_llm_axis_scores table.
    # This query efficiently checks for record count, distinct MCPs, and data freshness.
    sql_query = """
    SELECT
        COUNT(*) AS record_count,
        MAX(updated_at) AS latest_updated_at,
        MIN(updated_at) AS earliest_updated_at,
        COUNT(DISTINCT mcp_id) AS distinct_mcp_count
    FROM mcp_llm_axis_scores;
    """

    try:
        # Make a POST request to the write_service's query endpoint.
        # The SQL query is sent in the JSON body.
        response = requests.post(
            f"{WRITE_SERVICE_URL}{QUERY_ENDPOINT}",
            json={"query": sql_query},
            timeout=REQUEST_TIMEOUT_SECONDS
        )

        # Raise an HTTPError for bad responses (4xx or 5xx status codes).
        response.raise_for_status()

        # Parse the JSON response from the service.
        data = response.json()

        # Validate the basic structure of the response.
        if not data or not isinstance(data, list) or not data[0]:
            raise ValueError("No data returned from the query or unexpected response format.")

        # Assuming the aggregate query returns a single row (dictionary) in a list.
        result = data[0]

        record_count = result.get("record_count")
        latest_updated_at_str = result.get("latest_updated_at")
        earliest_updated_at_str = result.get("earliest_updated_at")
        distinct_mcp_count = result.get("distinct_mcp_count")

        print("\n--- Query Results from mcp_llm_axis_scores ---")
        print(f"  Total Records: {record_count if record_count is not None else 'N/A'}")
        print(f"  Distinct MCPs: {distinct_mcp_count if distinct_mcp_count is not None else 'N/A'}")
        print(f"  Latest Updated At: {latest_updated_at_str if latest_updated_at_str else 'N/A'}")
        print(f"  Earliest Updated At: {earliest_updated_at_str if earliest_updated_at_str else 'N/A'}")

        # --- Assertions for Data Population and Completeness ---

        # 1. Assert that the table is not empty.
        if record_count is None or record_count == 0:
            raise AssertionError("FAILURE: mcp_llm_axis_scores table is empty or 'record_count' is missing/zero.")
        print(f"  Assertion: Table is not empty (Record Count: {record_count}) - PASS")

        # 2. Assert a reasonable number of distinct MCPs.
        if distinct_mcp_count is None or distinct_mcp_count < MIN_MCP_COUNT:
            raise AssertionError(
                f"FAILURE: Insufficient distinct MCPs found. Expected at least {MIN_MCP_COUNT}, "
                f"but got {distinct_mcp_count}."
            )
        print(f"  Assertion: Sufficient distinct MCPs ({distinct_mcp_count} >= {MIN_MCP_COUNT}) - PASS")

        # --- Assertion for Data Freshness ---

        # 3. Assert that the data is recent.
        if latest_updated_at_str is None:
            raise AssertionError("FAILURE: 'latest_updated_at' timestamp is missing from the query results.")

        try:
            # Parse the timestamp string into a datetime object.
            # Handles ISO 8601 format, including optional 'Z' for UTC.
            # Ensures the datetime object is timezone-aware and in UTC for accurate comparison.
            latest_updated_at = datetime.datetime.fromisoformat(latest_updated_at_str.replace('Z', '+00:00'))
            if latest_updated_at.tzinfo is None:
                # If naive, assume UTC as per common database practice for 'updated_at'
                latest_updated_at = latest_updated_at.replace(tzinfo=datetime.timezone.utc)
            else:
                # Convert to UTC if it's already timezone-aware but in a different timezone
                latest_updated_at = latest_updated_at.astimezone(datetime.timezone.utc)

            # Get the current UTC time for comparison.
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            time_difference = now_utc - latest_updated_at
            max_age_timedelta = datetime.timedelta(hours=MAX_DATA_AGE_HOURS)

            if time_difference > max_age_timedelta:
                raise AssertionError(
                    f"FAILURE: Data is not fresh. Latest update was {time_difference} ago "
                    f"({latest_updated_at_str}), which is older than the allowed {MAX_DATA_AGE_HOURS} hours."
                )
            print(f"  Assertion: Data is fresh (Latest update {time_difference} ago) - PASS")

        except ValueError as e:
            raise AssertionError(f"FAILURE: Could not parse 'latest_updated_at' timestamp "
                                 f"'{latest_updated_at_str}': {e}")

        print("\nAll core scoring data availability checks passed for mcp_llm_axis_scores.")
        return True

    except requests.exceptions.ConnectionError as e:
        print(f"ERROR: Could not connect to the write service at {WRITE_SERVICE_URL}. "
              f"Please ensure the service is running and accessible.")
        print(f"Details: {e}")
        return False
    except requests.exceptions.Timeout as e:
        print(f"ERROR: Request to write service timed out after {REQUEST_TIMEOUT_SECONDS} seconds. "
              f"The service might be overloaded or unresponsive.")
        print(f"Details: {e}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"ERROR: An unexpected HTTP request error occurred: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response status: {e.response.status_code}")
            print(f"Response body: {e.response.text}")
        return False
    except AssertionError as e:
        # This catches our custom assertion failures
        print(f"{e}")
        return False
    except ValueError as e:
        # Catches issues with data parsing or unexpected formats
        print(f"ERROR: Data processing or format error: {e}")
        return False
    except Exception as e:
        # Catch any other unforeseen errors
        print(f"AN UNEXPECTED ERROR OCCURRED during verification: {e}")
        return False

if __name__ == "__main__":
    print("Executing MCP LLM Axis Scores Population Verification Script...")
    if verify_mcp_llm_axis_scores_population():
        print("\nVERIFICATION: PASS")
        sys.exit(0) # Exit with success code
    else:
        print("\nVERIFICATION: FAIL")
        sys.exit(1) # Exit with failure code