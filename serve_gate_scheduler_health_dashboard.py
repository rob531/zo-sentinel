# serve_gate_scheduler_health_dashboard.py

import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

app = FastAPI()

# Define the path to the HTML file
HTML_FILE_NAME = "gate_scheduler_health_dashboard_view.html"
# In a real application, you might use a more robust path resolution,
# e.g., os.path.join(os.path.dirname(__file__), "templates", HTML_FILE_NAME)
# For this task, we assume it's in the same directory or accessible.

@app.get("/dashboards/gate_scheduler_health", response_class=HTMLResponse)
async def serve_gate_scheduler_health_dashboard():
    """
    Serves the gate_scheduler_health_dashboard_view.html file.
    """
    try:
        with open(HTML_FILE_NAME, "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content, status_code=200)
    except FileNotFoundError:
        # If the HTML file is not found, return a 404 error
        return HTMLResponse(
            content=f"<h1>Error: Dashboard HTML file '{HTML_FILE_NAME}' not found.</h1>",
            status_code=404
        )
    except Exception as e:
        # Handle any other potential errors during file reading
        return HTMLResponse(
            content=f"<h1>Error loading dashboard: {e}</h1>",
            status_code=500
        )

if __name__ == "__main__":
    # --- Acceptance Test Block ---

    # Define the expected HTML content for the dummy file
    expected_html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gate Scheduler Health Dashboard</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f4f4f4; color: #333; }
        h1 { color: #0056b3; }
        .container { background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
    </style>
</head>
<body>
    <div class="container">
        <h1>Gate Scheduler Health Dashboard</h1>
        <p>This is a placeholder for the gate scheduler health dashboard content.</p>
        <p>Real-time metrics and status updates would appear here.</p>
        <div id="dashboard-root">
            <!-- Dynamic content would be injected here by a frontend framework -->
            <p>Loading dashboard data...</p>
        </div>
    </div>
</body>
</html>
"""
    # Create a dummy HTML file for the test to read
    # This ensures the `open()` call in the endpoint succeeds during the test.
    try:
        with open(HTML_FILE_NAME, "w", encoding="utf-8") as f:
            f.write(expected_html_content)

        client = TestClient(app)

        print(f"Testing GET /dashboards/gate_scheduler_health...")
        response = client.get("/dashboards/gate_scheduler_health")

        # Assert status code
        assert response.status_code == 200, \
            f"FAIL: Expected status code 200, but got {response.status_code}"

        # Assert content type
        assert response.headers["content-type"] == "text/html; charset=utf-8", \
            f"FAIL: Expected content-type 'text/html; charset=utf-8', but got {response.headers['content-type']}"

        # Assert HTML content
        # Normalize whitespace for robust comparison
        assert response.text.strip() == expected_html_content.strip(), \
            "FAIL: Returned HTML content does not match expected content."

        print("PASS")

    finally:
        # Clean up the dummy HTML file after the test
        if os.path.exists(HTML_FILE_NAME):
            os.remove(HTML_FILE_NAME)
            print(f"Cleaned up dummy file: {HTML_FILE_NAME}")