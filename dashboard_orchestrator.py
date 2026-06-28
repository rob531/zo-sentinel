# dashboard_orchestrator.py

import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

app = FastAPI()

# List of dashboard view files to aggregate.
# These files are expected to be in the same directory as this script.
# If they were served by local endpoints, 'requests.get(url).text' would be used instead.
DASHBOARD_VIEW_FILES = [
    "mcp_portfolio_health_dashboard_view.html",
    "self_diagnostics_dashboard_view.html",
    # Add more dashboard view files here as needed
]

@app.get("/dashboard/unified", response_class=HTMLResponse)
async def unified_dashboard():
    """
    Aggregates content from various _dashboard_view.html files
    into a single, unified dashboard view.
    """
    script_dir = Path(__file__).parent
    aggregated_content = []

    for filename in DASHBOARD_VIEW_FILES:
        file_path = script_dir / filename
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    aggregated_content.append(f.read())
            except IOError as e:
                # In a real application, this would be logged more robustly.
                print(f"Warning: Could not read dashboard file {filename}: {e}")
                aggregated_content.append(
                    f"<div class='dashboard-section error'>"
                    f"<h2>Error Loading: {filename}</h2>"
                    f"<p>Could not read content: {e}</p>"
                    f"</div>"
                )
        else:
            print(f"Warning: Dashboard file not found: {filename}")
            aggregated_content.append(
                f"<div class='dashboard-section missing'>"
                f"<h2>Content Missing: {filename}</h2>"
                f"<p>Dashboard content for {filename} is not available.</p>"
                f"</div>"
            )

    # Wrap the aggregated content in a basic HTML structure
    html_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Unified System Dashboard</title>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }}
            .dashboard-section {{ 
                background-color: #ffffff; 
                border: 1px solid #e0e0e0; 
                padding: 20px; 
                margin-bottom: 25px; 
                border-radius: 8px; 
                box-shadow: 0 2px 4px rgba(0,0,0,0.05); 
            }}
            .dashboard-section h2 {{ 
                color: #0056b3; 
                margin-top: 0; 
                border-bottom: 1px solid #eee; 
                padding-bottom: 10px; 
                margin-bottom: 15px; 
            }}
            .dashboard-section p, .dashboard-section ul {{ 
                line-height: 1.6; 
                color: #555; 
            }}
            .dashboard-section.error {{ border-color: #dc3545; background-color: #f8d7da; color: #721c24; }}
            .dashboard-section.error h2 {{ color: #dc3545; }}
            .dashboard-section.missing {{ border-color: #ffc107; background-color: #fff3cd; color: #856404; }}
            .dashboard-section.missing h2 {{ color: #ffc107; }}
            h1 {{ color: #2c3e50; text-align: center; margin-bottom: 30px; }}
            #unified-dashboard-container {{ max-width: 1000px; margin: 0 auto; }}
        </style>
    </head>
    <body>
        <h1>Unified System Dashboard</h1>
        <div id="unified-dashboard-container">
            {}
        </div>
    </body>
    </html>
    """.format("\n".join(aggregated_content))

    return HTMLResponse(content=html_template)

if __name__ == "__main__":
    # --- Setup: Create dummy HTML files for testing ---
    # These files will be created in the same directory as this script.
    dummy_files_content = {
        "mcp_portfolio_health_dashboard_view.html": """
            <div class="dashboard-section" id="mcp-health-dashboard">
                <h2>MCP Portfolio Health Overview</h2>
                <p>This is a unique string for MCP Health: <strong>MCP_HEALTH_DATA_XYZ_123</strong></p>
                <p>Current status: All systems nominal. No critical alerts detected.</p>
                <ul>
                    <li>Portfolio A: 98% healthy</li>
                    <li>Portfolio B: 95% healthy</li>
                </ul>
            </div>
        """,
        "self_diagnostics_dashboard_view.html": """
            <div class="dashboard-section" id="self-diagnostics-dashboard">
                <h2>System Self Diagnostics Report</h2>
                <p>This is a unique string for Self Diagnostics: <strong>SELF_DIAG_STATUS_ABC_456</strong></p>
                <p>Last run: 2023-10-27 10:30:00 UTC</p>
                <ul>
                    <li>CPU Usage: 25% (Normal)</li>
                    <li>Memory Usage: 40% (Normal)</li>
                    <li>Disk Space: 70% free (Healthy)</li>
                    <li>Network Latency: 10ms (Optimal)</li>
                </ul>
            </div>
        """
    }

    script_dir = Path(__file__).parent
    created_files = []
    for filename, content in dummy_files_content.items():
        file_path = script_dir / filename
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content.strip())
            created_files.append(file_path)
            print(f"Created dummy file: {file_path}")
        except IOError as e:
            print(f"Error creating dummy file {file_path}: {e}")

    # --- Test Execution ---
    client = TestClient(app)
    response = client.get("/dashboard/unified")

    # --- Assertions ---
    try:
        # 1. Assert status code is 200 OK
        assert response.status_code == 200, \
            f"FAIL: Expected status code 200, but got {response.status_code}"
        print("Assertion Passed: Status code is 200 OK.")

        response_text = response.text

        # 2. Assert unique strings from at least two distinct embedded dashboard HTML files
        unique_string_1 = "MCP_HEALTH_DATA_XYZ_123"
        unique_string_2 = "SELF_DIAG_STATUS_ABC_456"

        assert unique_string_1 in response_text, \
            f"FAIL: Unique string '{unique_string_1}' not found in response."
        print(f"Assertion Passed: Found unique string 1 ('{unique_string_1}').")

        assert unique_string_2 in response_text, \
            f"FAIL: Unique string '{unique_string_2}' not found in response."
        print(f"Assertion Passed: Found unique string 2 ('{unique_string_2}').")

        print("\nPASS")

    except AssertionError as e:
        print(f"\n{e}")
    finally:
        # --- Cleanup: Remove dummy files ---
        for file_path in created_files:
            try:
                os.remove(file_path)
                print(f"Cleaned up dummy file: {file_path}")
            except OSError as e:
                print(f"Error cleaning up dummy file {file_path}: {e}")