import os
import shutil
from fastapi import FastAPI, APIRouter
from fastapi.testclient import TestClient

# --- Configuration ---
# The directory to scan for dashboard views.
# This will be created and populated for testing purposes in the __main__ block.
ZO_SENTINEL_DIR = "zo_sentinel"

# --- FastAPI App Setup ---
app = FastAPI(title="Dashboard Listing API")
router = APIRouter(prefix="/dashboards")

# --- Helper Functions ---

def is_dashboard_view(filepath: str) -> bool:
    """
    Checks if a given HTML file is identified as a dashboard view.
    A file is considered a dashboard view if:
    1. 'dashboard_view' (case-insensitive) is present in its filename.
    2. OR 'dashboard_view' (case-insensitive) is present in its content.
    """
    filename = os.path.basename(filepath)

    # 1. Check filename for 'dashboard_view'
    if "dashboard_view" in filename.lower():
        return True

    # 2. Check content for 'dashboard_view' (only for HTML files)
    # This check is performed only if the filename itself doesn't contain the keyword.
    # It also ensures we only attempt to read HTML files for content.
    if not filename.lower().endswith(".html"):
        return False # Only HTML files are considered for content check

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            if "dashboard_view" in content.lower():
                return True
    except Exception as e:
        # Log or handle potential errors during file reading (e.g., permission issues)
        # For this task, we'll just return False if an error occurs.
        print(f"Error reading file {filepath}: {e}")
        pass
    return False

def scan_dashboard_files() -> list[dict]:
    """
    Scans the `ZO_SENTINEL_DIR` for HTML files identified as dashboard views.
    Returns a list of dictionaries, each containing the 'filename' and
    'relative_path' of an identified dashboard view.
    """
    dashboard_files = []
    if not os.path.exists(ZO_SENTINEL_DIR):
        print(f"Warning: Directory '{ZO_SENTINEL_DIR}' does not exist. Returning empty list.")
        return []

    # Walk through the directory tree
    for root, _, files in os.walk(ZO_SENTINEL_DIR):
        for file in files:
            # Only process HTML files
            if file.lower().endswith(".html"):
                full_filepath = os.path.join(root, file)
                if is_dashboard_view(full_filepath):
                    # Calculate path relative to ZO_SENTINEL_DIR
                    relative_path = os.path.relpath(full_filepath, ZO_SENTINEL_DIR)
                    dashboard_files.append({
                        "filename": file,
                        "relative_path": relative_path
                    })
    return dashboard_files

# --- FastAPI Endpoints ---

@router.get("/list", summary="List all identified dashboard view HTML files")
async def list_dashboards():
    """
    Scans the `zo_sentinel` directory for HTML files identified as dashboard views.
    Returns a JSON list of these dashboard filenames and their relative paths.
    This endpoint does not access any database.
    """
    return scan_dashboard_files()

# --- Register Router ---
app.include_router(router)

# --- Test Client in __main__ ---
if __name__ == "__main__":
    # --- Setup: Create a dummy zo_sentinel directory and files for testing ---
    print(f"Setting up test directory: {ZO_SENTINEL_DIR}")
    # Ensure a clean state by removing any existing test directory
    if os.path.exists(ZO_SENTINEL_DIR):
        shutil.rmtree(ZO_SENTINEL_DIR)
    
    # Create the base directory and a sub-folder
    os.makedirs(os.path.join(ZO_SENTINEL_DIR, "sub_folder"), exist_ok=True)

    # Create a dashboard view file (by filename)
    with open(os.path.join(ZO_SENTINEL_DIR, "overview_dashboard_view.html"), "w", encoding="utf-8") as f:
        f.write("<h1>Overview Dashboard</h1><p>This is a key dashboard_view.</p>")

    # Create another dashboard view file in a sub-folder (by content)
    with open(os.path.join(ZO_SENTINEL_DIR, "sub_folder", "another_dashboard.html"), "w", encoding="utf-8") as f:
        f.write("<html><body><p>Content for another dashboard_view.</p></body></html>")

    # Create a dashboard view file (by content, filename does not contain keyword)
    with open(os.path.join(ZO_SENTINEL_DIR, "content_based_report.html"), "w", encoding="utf-8") as f:
        f.write("<html><body><p>This page contains a dashboard_view in its content.</p></body></html>")

    # Create a non-dashboard HTML file (should NOT be listed)
    with open(os.path.join(ZO_SENTINEL_DIR, "regular_page.html"), "w", encoding="utf-8") as f:
        f.write("<h1>Just a regular page</h1>")

    # Create a non-HTML file with 'dashboard_view' in content (should be ignored as it's not HTML)
    with open(os.path.join(ZO_SENTINEL_DIR, "dashboard_info.txt"), "w", encoding="utf-8") as f:
        f.write("This text file contains dashboard_view information.")

    print("Running tests with FastAPI TestClient...")
    client = TestClient(app)
    response = client.get("/dashboards/list")

    # --- Assertions ---
    assert response.status_code == 200, f"Expected status code 200, but got {response.status_code}"
    data = response.json()

    assert isinstance(data, list), "Expected response to be a list"
    assert len(data) > 0, "Expected a non-empty list of dashboard files"
    assert len(data) == 3, f"Expected 3 dashboard files, but got {len(data)}" # Based on our setup

    # Helper to check if a specific file is in the results
    def find_dashboard_in_results(filename: str, expected_path: str) -> bool:
        for item in data:
            if item.get("filename") == filename:
                assert item.get("relative_path") == expected_path, \
                    f"Incorrect relative_path for {filename}: Expected '{expected_path}', got '{item.get('relative_path')}'"
                return True
        return False

    # Check for specific known dashboard views
    assert find_dashboard_in_results("overview_dashboard_view.html", "overview_dashboard_view.html"), \
        "overview_dashboard_view.html not found in the list"
    
    assert find_dashboard_in_results("another_dashboard.html", os.path.join("sub_folder", "another_dashboard.html")), \
        "another_dashboard.html not found in the list"

    assert find_dashboard_in_results("content_based_report.html", "content_based_report.html"), \
        "content_based_report.html not found in the list"

    # Ensure non-dashboard files are NOT included
    non_dashboard_files = ["regular_page.html", "dashboard_info.txt"]
    for item in data:
        assert item.get("filename") not in non_dashboard_files, \
            f"Non-dashboard file '{item.get('filename')}' was incorrectly included."

    print("PASS")

    # --- Cleanup: Remove the dummy zo_sentinel directory ---
    print(f"Cleaning up test directory: {ZO_SENTINEL_DIR}")
    shutil.rmtree(ZO_SENTINEL_DIR)