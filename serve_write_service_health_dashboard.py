# serve_write_service_health_dashboard.py
from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from pathlib import Path
from starlette.testclient import TestClient
import os

# Define the FastAPI router
router = APIRouter()

# Determine the base directory for templates.
# This assumes a directory structure like:
# project_root/
# ├── serve_write_service_health_dashboard.py
# └── templates/
#     └── write_service_health_dashboard_view.html
#
# Path(__file__).parent gets the directory where this script resides.
current_file_dir = Path(__file__).parent
templates_dir = current_file_dir / "templates"
html_file_path = templates_dir / "write_service_health_dashboard_view.html"

@router.get("/dashboard/write_service_health", summary="Serve the write service health dashboard")
async def get_write_service_health_dashboard():
    """
    Serves the HTML dashboard for write service health.
    """
    if not html_file_path.is_file():
        # In a production environment, you might log this error
        # and serve a more informative error page or raise an HTTPException.
        # For this exercise, we assume the file will be present.
        # If not, FileResponse will raise an error or the test will fail.
        # Returning a 404 with a generic message for robustness.
        return FileResponse(
            "path/to/a/default_404_page.html",
            media_type="text/html",
            status_code=404,
            # A simple fallback content if no 404 page exists
            # This is a simplification; a real app would have a proper 404 handler.
            content="<h1>404 Not Found</h1><p>Dashboard file not found.</p>"
        )
    return FileResponse(html_file_path, media_type="text/html")

# Acceptance Test Block
if __name__ == "__main__":
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # --- Setup: Create dummy HTML file and directory for testing ---
    # Ensure the 'templates' directory exists for the test
    test_templates_dir = current_file_dir / "templates"
    test_html_file_path = test_templates_dir / "write_service_health_dashboard_view.html"
    
    # Content for the dummy HTML file
    test_html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Write Service Health Dashboard</title>
    <style>
        body { font-family: sans-serif; margin: 20px; background-color: #f4f4f4; color: #333; }
        h1 { color: #0056b3; border-bottom: 2px solid #0056b3; padding-bottom: 10px; }
        .status-ok { color: green; font-weight: bold; }
        .status-error { color: red; font-weight: bold; }
        div { background-color: #fff; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-top: 20px; }
        ul { list-style-type: none; padding: 0; }
        li { margin-bottom: 5px; }
    </style>
</head>
<body>
    <h1>Write Service Health Dashboard</h1>
    <p>Current Status: <span class="status-ok">Operational</span></p>
    <p>Last updated: 2023-10-27 10:30:00 UTC</p>
    <div>
        <h2>Key Metrics</h2>
        <ul>
            <li>Writes per second: 1200</li>
            <li>Error rate: 0.01%</li>
            <li>Latency (p99): 50ms</li>
            <li>Disk Usage: 75%</li>
        </ul>
    </div>
    <p><small>Data refreshed every 60 seconds.</small></p>
</body>
</html>
"""
    
    test_templates_dir.mkdir(exist_ok=True) # Create 'templates' directory if it doesn't exist
    with open(test_html_file_path, "w") as f:
        f.write(test_html_content)
    # --- End Setup ---

    print(f"Running acceptance test for GET /dashboard/write_service_health...")
    response = client.get("/dashboard/write_service_health")

    # Assertions
    assert response.status_code == 200, \
        f"Test failed: Expected status code 200, but got {response.status_code}"
    
    # FileResponse typically adds 'charset=utf-8' by default for text types
    assert response.headers["content-type"] == "text/html; charset=utf-8", \
        f"Test failed: Expected content-type 'text/html; charset=utf-8', but got '{response.headers['content-type']}'"
    
    # Compare the response text with the expected content, stripping whitespace for robust comparison
    assert response.text.strip() == test_html_content.strip(), \
        "Test failed: Response content does not match the expected HTML content."

    print("PASS")

    # --- Teardown: Clean up dummy HTML file and directory ---
    os.remove(test_html_file_path)
    # Attempt to remove the 'templates' directory only if it's empty
    try:
        os.rmdir(test_templates_dir)
    except OSError:
        # If the directory is not empty (e.g., other files were present or created),
        # or if it's still in use, rmdir will fail. This is acceptable for cleanup.
        pass
    # --- End Teardown ---