# verify_mcp_definition_history_view_integration.py

import os
import sys
import tempfile
import shutil
from contextlib import contextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from bs4 import BeautifulSoup

# --- 1. Mock Data (simulating mcp_definition_history_report_api.py output) ---
# This data would typically come from a database query via the report API.
MOCK_REPORT_DATA = [
    {"mcp_id": 1, "mcp_name": "Core Network MCP", "version": "1.0.0", "change_date": "2023-01-15", "changed_by": "Alice Smith", "description": "Initial release"},
    {"mcp_id": 2, "mcp_name": "Edge Services MCP", "version": "1.0.0", "change_date": "2023-02-01", "changed_by": "Bob Johnson", "description": "First version"},
    {"mcp_id": 1, "mcp_name": "Core Network MCP", "version": "1.0.1", "change_date": "2023-03-10", "changed_by": "Charlie Brown", "description": "Bug fixes and performance improvements"},
    {"mcp_id": 3, "mcp_name": "Security Gateway MCP", "version": "1.0.0", "change_date": "2023-04-05", "changed_by": "Diana Prince", "description": "New security features"},
]

# --- 2. Mock HTML Template Content (simulating mcp_definition_history_view.html) ---
# This template expects 'history_data' to be passed to it.
MOCK_HTML_TEMPLATE_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MCP Definition History</title>
    <style>
        body { font-family: sans-serif; margin: 20px; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
    </style>
</head>
<body>
    <h1>MCP Definition History Report</h1>

    <div id="report-container">
        <p>Displaying history for all MCP definitions.</p>
        <table id="mcpHistoryTable">
            <thead>
                <tr>
                    <th>MCP ID</th>
                    <th>MCP Name</th>
                    <th>Version</th>
                    <th>Change Date</th>
                    <th>Changed By</th>
                    <th>Description</th>
                </tr>
            </thead>
            <tbody>
                {% for item in history_data %}
                <tr>
                    <td>{{ item.mcp_id }}</td>
                    <td>{{ item.mcp_name }}</td>
                    <td>{{ item.version }}</td>
                    <td>{{ item.change_date }}</td>
                    <td>{{ item.changed_by }}</td>
                    <td>{{ item.description }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
"""

# --- 3. Mock FastAPI Application Setup ---
# We use a context manager to handle the temporary directory for templates.
@contextmanager
def setup_mock_fastapi_app():
    """
    Sets up a temporary FastAPI application with a mock template directory.
    Yields a TestClient instance for the application.
    """
    temp_templates_dir = None
    try:
        temp_templates_dir = tempfile.mkdtemp()
        template_path = os.path.join(temp_templates_dir, "mcp_definition_history_view.html")
        with open(template_path, "w") as f:
            f.write(MOCK_HTML_TEMPLATE_CONTENT)

        app = FastAPI()
        templates = Jinja2Templates(directory=temp_templates_dir)

        @app.get("/mcp_definition_history_view", response_class=HTMLResponse)
        async def get_mcp_history_view(request: Request):
            """
            Simulates the FastAPI endpoint that serves the history view.
            It uses the MOCK_REPORT_DATA directly, mimicking the data
            that would come from mcp_definition_history_report_api.py.
            """
            history_data = MOCK_REPORT_DATA
            return templates.TemplateResponse(
                "mcp_definition_history_view.html",
                {"request": request, "history_data": history_data}
            )

        yield TestClient(app)
    finally:
        if temp_templates_dir and os.path.exists(temp_templates_dir):
            shutil.rmtree(temp_templates_dir)
            print(f"Cleaned up temporary template directory: {temp_templates_dir}")

# --- 4. Verification Logic ---
def verify_integration():
    """
    Performs the end-to-end integration verification.
    """
    print("Starting MCP Definition History View integration verification...")

    with setup_mock_fastapi_app() as client:
        print("Mock FastAPI app and templates set up.")

        # Make a GET request to the endpoint
        response = client.get("/mcp_definition_history_view")

        # Assert HTTP status code
        assert response.status_code == 200, \
            f"Expected HTTP status code 200, but got {response.status_code}. Response: {response.text[:200]}..."
        print(f"Successfully received HTTP 200 response from '/mcp_definition_history_view'.")

        # Parse the HTML response
        soup = BeautifulSoup(response.text, 'html.parser')

        # Assert page title
        title_tag = soup.find('title')
        assert title_tag and "MCP Definition History" in title_tag.text, \
            f"Expected page title 'MCP Definition History', but got '{title_tag.text if title_tag else 'None'}'."
        print(f"Page title '{title_tag.text}' found and correct.")

        # Assert main heading
        h1_tag = soup.find('h1')
        assert h1_tag and "MCP Definition History Report" in h1_tag.text, \
            f"Expected H1 heading 'MCP Definition History Report', but got '{h1_tag.text if h1_tag else 'None'}'."
        print(f"Main heading '{h1_tag.text}' found and correct.")

        # Locate the data table
        table = soup.find('table', id='mcpHistoryTable')
        assert table, "Could not find table with id 'mcpHistoryTable' in the HTML response."
        print("Table with id 'mcpHistoryTable' found.")

        tbody = table.find('tbody')
        assert tbody, "Could not find <tbody> within the 'mcpHistoryTable'."
        print("Table body found.")

        rows = tbody.find_all('tr')
        assert len(rows) == len(MOCK_REPORT_DATA), \
            f"Expected {len(MOCK_REPORT_DATA)} data rows, but found {len(rows)}."
        print(f"Found {len(rows)} data rows, matching the mock data count.")

        # Assert the content of each row against the mock data
        expected_keys = ["mcp_id", "mcp_name", "version", "change_date", "changed_by", "description"]
        for i, expected_item in enumerate(MOCK_REPORT_DATA):
            row = rows[i]
            cells = row.find_all('td')
            assert len(cells) == len(expected_keys), \
                f"Row {i} expected {len(expected_keys)} cells, but found {len(cells)}."

            actual_data = {key: cells[j].text.strip() for j, key in enumerate(expected_keys)}

            # Convert mcp_id to string for comparison as HTML text content is always string
            expected_item_str = {k: str(v) for k, v in expected_item.items()}

            for key in expected_keys:
                assert actual_data[key] == expected_item_str[key], \
                    f"Row {i}, column '{key}': Expected '{expected_item_str[key]}', but got '{actual_data[key]}'."
            print(f"Row {i} data verified: {actual_data['mcp_name']} (v{actual_data['version']})")

    print("\nAll data points from mcp_definition_history_report_api.py correctly embedded and displayed in mcp_definition_history_view.html!")

# --- 5. Main Execution Block ---
if __name__ == '__main__':
    try:
        verify_integration()
        print("\nPASS: MCP Definition History View integration verified successfully!")
    except AssertionError as e:
        print(f"\nFAIL: Integration verification failed - {e}")
        sys.exit(1)  # Indicate failure to the system
    except Exception as e:
        print(f"\nERROR: An unexpected error occurred during verification - {e}")
        sys.exit(1)