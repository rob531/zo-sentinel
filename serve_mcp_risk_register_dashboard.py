# serve_mcp_risk_register_dashboard.py

import os
import uvicorn
import httpx
import multiprocessing
import time
from fastapi import FastAPI, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles # Included as per prompt, though HTMLResponse is used for the specific file.

# --- Configuration for the HTML file ---
# Define the directory for templates relative to this script
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
HTML_FILE_NAME = "mcp_risk_register_dashboard_view.html"
HTML_FILE_PATH = os.path.join(TEMPLATES_DIR, HTML_FILE_NAME)

# Dummy HTML content for testing purposes
DUMMY_HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MCP Risk Register Dashboard</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        .container { max-width: 900px; margin: auto; background: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        h1 { color: #2c3e50; text-align: center; margin-bottom: 25px; }
        p { line-height: 1.6; color: #555; }
        .risk-item { background-color: #ecf0f1; border-left: 5px solid #3498db; margin-bottom: 15px; padding: 15px; border-radius: 4px; }
        .risk-item strong { color: #2980b9; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Welcome to the MCP Risk Register Dashboard!</h1>
        <p>This dashboard provides an overview of the current risks in the system.</p>
        <div class="risk-item">
            <p><strong>Risk ID:</strong> R001</p>
            <p><strong>Description:</strong> Critical system vulnerability identified.</p>
            <p><strong>Status:</strong> Open</p>
            <p><strong>Severity:</strong> High</p>
        </div>
        <div class="risk-item">
            <p><strong>Risk ID:</strong> R002</p>
            <p><strong>Description:</strong> Data integrity issue in reporting module.</p>
            <p><strong>Status:</strong> Closed</p>
            <p><strong>Severity:</strong> Medium</p>
        </div>
        <p>For more details, please refer to the full risk register document.</p>
    </div>
</body>
</html>
"""

# --- FastAPI Application ---
app = FastAPI(
    title="MCP Risk Register Dashboard Service",
    description="FastAPI service to expose the MCP Risk Register Dashboard.",
    version="1.0.0",
)

# Create an API router with a prefix for dashboard endpoints
router = APIRouter(prefix="/dashboard")

@router.get("/risk_register", response_class=HTMLResponse, summary="Get MCP Risk Register Dashboard")
async def get_mcp_risk_register_dashboard():
    """
    Serves the `mcp_risk_register_dashboard_view.html` file.
    This endpoint reads the HTML content from the specified file and returns it
    as an HTMLResponse.
    """
    if not os.path.exists(HTML_FILE_PATH):
        # In a production environment, you might want to log this error
        # and return a more user-friendly error page or an HTTPException.
        error_content = "<h1>Error: Dashboard template not found!</h1><p>Please ensure 'mcp_risk_register_dashboard_view.html' exists in the 'templates' directory.</p>"
        return HTMLResponse(content=error_content, status_code=500)
    
    with open(HTML_FILE_PATH, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

# Include the router in the main FastAPI application
app.include_router(router)

# --- Main block for testing and running the server ---
if __name__ == "__main__":
    # 1. Ensure the templates directory exists
    os.makedirs(TEMPLATES_DIR, exist_ok=True)

    # 2. Create the dummy HTML file for testing
    with open(HTML_FILE_PATH, "w", encoding="utf-8") as f:
        f.write(DUMMY_HTML_CONTENT)

    # Function to run the Uvicorn server
    def run_server():
        """Runs the FastAPI application using Uvicorn."""
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")

    # Start the server in a separate process to allow the main thread to run tests
    server_process = multiprocessing.Process(target=run_server)
    server_process.start()

    # Give the server a moment to start up
    time.sleep(2) 

    try:
        # 3. Make a GET request to the endpoint using httpx
        print(f"Attempting to connect to http://127.0.0.1:8000/dashboard/risk_register...")
        response = httpx.get("http://127.0.0.1:8000/dashboard/risk_register")

        # 4. Assertions
        assert response.status_code == 200, \
            f"FAIL: Expected status code 200, but got {response.status_code}. Response: {response.text[:200]}..."
        
        # Check if a significant part of the dummy content is present
        # Using 'in' for robustness against minor formatting differences
        assert "<h1>Welcome to the MCP Risk Register Dashboard!</h1>" in response.text, \
            "FAIL: Expected dashboard title not found in response."
        assert "<p><strong>Risk ID:</strong> R001</p>" in response.text, \
            "FAIL: Expected risk item content not found in response."
        
        print("PASS: GET /dashboard/risk_register returned 200 OK and the expected HTML content.")

    except httpx.RequestError as e:
        print(f"FAIL: Could not connect to the server: {e}")
    except AssertionError as e:
        print(e)
    except Exception as e:
        print(f"FAIL: An unexpected error occurred during testing: {e}")
    finally:
        # 5. Terminate the server process
        print("Shutting down server...")
        server_process.terminate()
        server_process.join() # Wait for the process to terminate gracefully

        # 6. Clean up the dummy HTML file and directory
        if os.path.exists(HTML_FILE_PATH):
            os.remove(HTML_FILE_PATH)
            print(f"Cleaned up: Removed '{HTML_FILE_NAME}'.")
        # Only remove the templates directory if it's empty
        if os.path.exists(TEMPLATES_DIR) and not os.listdir(TEMPLATES_DIR):
            os.rmdir(TEMPLATES_DIR)
            print(f"Cleaned up: Removed empty directory '{os.path.basename(TEMPLATES_DIR)}'.")