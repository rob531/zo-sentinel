# mcp_submissions_dashboard_integrator.py

import datetime
import random
import logging

# Configure logging for the integrator module
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Simulated External Modules (In a real scenario, these would be actual imports) ---

# Simulate mcp_submissions_api module
class MCPAPIError(Exception):
    """Custom exception for errors originating from the MCP Submissions API."""
    pass

class mcp_submissions_api:
    """
    Simulates the external API module for fetching MCP submission data.
    """
    API_ENDPOINT = "https://api.mcp.com/submissions" # Example endpoint

    @staticmethod
    def get_all_submissions():
        """
        Fetches all submission records from the simulated MCP API.
        
        Returns:
            list[dict]: A list of dictionaries, each representing a raw submission.
        
        Raises:
            MCPAPIError: If there's a simulated issue connecting to or retrieving data from the API.
        """
        logging.info(f"Attempting to fetch data from {mcp_submissions_api.API_ENDPOINT}")
        
        # Simulate network latency or API unreliability
        if random.random() < 0.15:  # 15% chance of simulated API failure
            logging.error("Simulated API connection failure or timeout.")
            raise MCPAPIError("Failed to connect to MCP Submissions API or API is unavailable.")
        
        # Simulate data retrieval
        # In a real scenario, this would involve HTTP requests (e.g., using 'requests' library)
        # and parsing JSON responses.
        submissions = [
            {
                "submission_id": "sub_001",
                "user_identifier": "user_A",
                "project_title": "Project Alpha",
                "status_code": 1, # 1: Pending, 2: Approved, 3: Rejected, 4: In Review
                "submitted_at": "2023-01-15T10:00:00Z",
                "last_modified": "2023-01-15T10:00:00Z",
                "data_hash": "abc123xyz789"
            },
            {
                "submission_id": "sub_002",
                "user_identifier": "user_B",
                "project_title": "Project Beta",
                "status_code": 2,
                "submitted_at": "2023-01-16T11:30:00Z",
                "last_modified": "2023-01-16T11:30:00Z",
                "data_hash": "def456uvw012"
            },
            {
                "submission_id": "sub_003",
                "user_identifier": "user_A",
                "project_title": "Project Gamma",
                "status_code": 3,
                "submitted_at": "2023-01-17T14:15:00Z",
                "last_modified": "2023-01-17T14:30:00Z",
                "data_hash": "ghi789rst345"
            },
            {
                "submission_id": "sub_004",
                "user_identifier": "user_C",
                "project_title": "Project Delta",
                "status_code": 1,
                "submitted_at": "2023-01-18T09:00:00Z",
                "last_modified": "2023-01-18T09:00:00Z",
                "data_hash": "jkl012mno678"
            },
            {
                "submission_id": "sub_005",
                "user_identifier": "user_D",
                "project_title": "Project Epsilon",
                "status_code": 4,
                "submitted_at": "2023-01-19T16:00:00Z",
                "last_modified": "2023-01-19T17:00:00Z",
                "data_hash": "pqr345stu901"
            }
        ]
        logging.info(f"Successfully fetched {len(submissions)} submissions from API.")
        return submissions

# Simulate mcp_submissions_dashboard_view module
class mcp_submissions_dashboard_view:
    """
    Simulates the external dashboard view module responsible for rendering data.
    """
    @staticmethod
    def render_dashboard(data: list[dict]):
        """
        Simulates rendering the dashboard with formatted submission data.
        
        Args:
            data (list[dict]): A list of dictionaries, each formatted for display.
                               Expected keys: 'ID', 'User', 'Project', 'Status', 
                               'Submitted On', 'Last Updated'.
        """
        logging.info("Rendering dashboard with provided data.")
        print("\n--- MCP Submissions Dashboard ---")
        if not data:
            print("No submission data available to display.")
            return

        # Determine column widths for pretty printing
        headers = list(data[0].keys())
        col_widths = {header: max(len(header), max(len(str(item.get(header, ''))) for item in data)) for header in headers}

        # Print header
        header_line = " | ".join(header.ljust(col_widths[header]) for header in headers)
        print(header_line)
        print("-+-".join("-" * col_widths[header] for header in headers))

        # Print data rows
        for item in data:
            row_line = " | ".join(str(item.get(header, '')).ljust(col_widths[header]) for header in headers)
            print(row_line)
        print("---------------------------------\n")
        logging.info("Dashboard rendering complete.")

    @staticmethod
    def display_error(message: str):
        """
        Simulates displaying an error message prominently on the dashboard.
        
        Args:
            message (str): The error message to display.
        """
        logging.error(f"Displaying dashboard error: {message}")
        print(f"\n--- Dashboard Error ---")
        print(f"ERROR: {message}")
        print(f"Please try refreshing or contact support if the issue persists.")
        print(f"-----------------------\n")

# --- Integrator Module Logic ---

# Mapping for API status codes to human-readable strings
STATUS_CODE_MAP = {
    1: "Pending Review",
    2: "Approved",
    3: "Rejected",
    4: "In Review",
    # Add more status codes as defined by the API
}

# Standard date/time format for dashboard display
DASHBOARD_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

def _format_datetime_for_dashboard(iso_datetime_str: str) -> str:
    """
    Converts an ISO 8601 datetime string (e.g., "2023-01-15T10:00:00Z")
    to a human-readable format suitable for the dashboard.
    
    Args:
        iso_datetime_str (str): The datetime string from the API.
        
    Returns:
        str: Formatted datetime string.
    """
    try:
        # Handle 'Z' (Zulu time) by replacing with '+00:00' for fromisoformat
        dt_object = datetime.datetime.fromisoformat(iso_datetime_str.replace('Z', '+00:00'))
        return dt_object.strftime(DASHBOARD_DATETIME_FORMAT)
    except ValueError as e:
        logging.warning(f"Could not parse datetime string '{iso_datetime_str}': {e}")
        return "Invalid Date"

def format_submission_for_dashboard(raw_submission: dict) -> dict:
    """
    Transforms a single raw submission dictionary from the API into a format
    suitable for the mcp_submissions_dashboard_view.
    
    Args:
        raw_submission (dict): A dictionary representing a single submission
                               as returned by the mcp_submissions_api.
                               
    Returns:
        dict: A dictionary with keys and values formatted for dashboard display.
    """
    logging.debug(f"Formatting raw submission: {raw_submission.get('submission_id', 'N/A')}")
    
    return {
        "ID": raw_submission.get("submission_id", "N/A"),
        "User": raw_submission.get("user_identifier", "Unknown User"),
        "Project": raw_submission.get("project_title", "Untitled Project"),
        "Status": STATUS_CODE_MAP.get(raw_submission.get("status_code"), "Unknown Status"),
        "Submitted On": _format_datetime_for_dashboard(raw_submission.get("submitted_at", "")),
        "Last Updated": _format_datetime_for_dashboard(raw_submission.get("last_modified", "")),
    }

def integrate_and_display_submissions():
    """
    Main integration function. Fetches data from the MCP Submissions API,
    formats it, and passes it to the dashboard view for rendering.
    Handles potential API errors and other exceptions gracefully.
    """
    logging.info("Starting MCP Submissions Dashboard Integration process.")
    
    try:
        # 1. Fetch data from the API
        raw_submissions = mcp_submissions_api.get_all_submissions()
        
        # 2. Format data for the dashboard view
        formatted_submissions = [
            format_submission_for_dashboard(sub) for sub in raw_submissions
        ]
        logging.info(f"Successfully formatted {len(formatted_submissions)} submissions for dashboard.")
        
        # 3. Render data in the dashboard view
        mcp_submissions_dashboard_view.render_dashboard(formatted_submissions)
        
    except MCPAPIError as e:
        # Handle API-specific errors
        error_message = f"Failed to retrieve submissions from API: {e}"
        logging.exception(error_message) # Log full traceback for API errors
        mcp_submissions_dashboard_view.display_error(error_message)
        
    except Exception as e:
        # Catch any other unexpected errors during data processing or rendering
        error_message = f"An unexpected error occurred during integration: {e}"
        logging.exception(error_message) # Log full traceback for unexpected errors
        mcp_submissions_dashboard_view.display_error(error_message)
        
    logging.info("MCP Submissions Dashboard Integration process finished.")

# --- Entry Point for the Module ---
if __name__ == "__main__":
    print("Running MCP Submissions Dashboard Integrator directly...")
    
    # Run the integration process
    integrate_and_display_submissions()
    
    # Simulate another run to demonstrate error handling (might hit API error)
    print("\n--- Simulating another run (15% chance of API error) ---")
    integrate_and_display_submissions()

    print("\n--- Simulating a third run (15% chance of API error) ---")
    integrate_and_display_submissions()