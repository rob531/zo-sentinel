import importlib
import os
import sys
from typing import Dict, List, Any

# Define the expected routes for verification.
# Each entry should specify:
# - 'path': The URL path.
# - 'methods': A list of HTTP methods (e.g., ['GET'], ['POST']).
# - 'handler_name': The name of the Python function expected to handle this route.
# - 'type': A descriptive type (e.g., 'UI', 'API').
#
# This list serves as the "manifest of expected dashboard views and APIs".
EXPECTED_ROUTES = [
    {'path': '/dashboard', 'methods': ['GET'], 'handler_name': 'render_dashboard', 'type': 'UI'},
    {'path': '/settings', 'methods': ['GET'], 'handler_name': 'render_settings', 'type': 'UI'},
    {'path': '/api/status', 'methods': ['GET'], 'handler_name': 'get_status', 'type': 'API'},
    {'path': '/api/config', 'methods': ['POST'], 'handler_name': 'update_config', 'type': 'API'},
    {'path': '/api/logs', 'methods': ['GET'], 'handler_name': 'get_logs', 'type': 'API'},
    # Example of an expected route that might be intentionally missing or misconfigured
    # for testing purposes:
    # {'path': '/admin', 'methods': ['GET'], 'handler_name': 'render_admin_panel', 'type': 'UI'}, # Missing route
    # {'path': '/api/data', 'methods': ['GET'], 'handler_name': 'get_data_v2', 'type': 'API'}, # Mismatched handler
]

def verify_wiring() -> Dict[str, List[Any]]:
    """
    Verifies the complete wiring of the `app_router_registry.py` by dynamically
    inspecting its registered routes and comparing them against a manifest of
    expected dashboard views and APIs.

    Returns:
        A dictionary detailing missing, misconfigured, or unexpected routes.
        The dictionary has the following keys:
        - 'missing_routes': List of expected routes that were not found.
        - 'misconfigured_routes': List of expected routes found, but with
                                  an incorrect handler function.
        - 'unexpected_routes': List of routes found in the registry but not
                               in the expected manifest.
    """
    discrepancies: Dict[str, List[Any]] = {
        'missing_routes': [],
        'misconfigured_routes': [],
        'unexpected_routes': []
    }

    try:
        # Dynamically import the app_router_registry module
        app_router_module = importlib.import_module('app_router_registry')
        app_router = app_router_module.app_router
    except ImportError:
        discrepancies['missing_routes'].append(
            "Could not import 'app_router_registry.py'. Ensure it exists and is in sys.path."
        )
        return discrepancies
    except AttributeError:
        discrepancies['missing_routes'].append(
            "Could not find 'app_router' Blueprint in 'app_router_registry.py'."
        )
        return discrepancies

    # Extract actual registered routes from the Blueprint
    actual_routes_map: Dict[tuple, str] = {}
    for rule in app_router.url_rules:
        # Flask rules often include HEAD and OPTIONS by default for GET routes.
        # We filter these out to match explicit method definitions.
        methods = [m for m in rule.methods if m not in {'HEAD', 'OPTIONS'}]

        # Skip rules without explicit HTTP methods (e.g., static files, internal rules)
        if not methods:
            continue

        # The endpoint attribute typically holds the name of the handler function
        handler_name = rule.endpoint

        # Create a canonical key for comparison: (path, frozenset_of_methods)
        # Using frozenset for methods ensures order-independent comparison
        key = (rule.rule, frozenset(methods))
        actual_routes_map[key] = handler_name

    # Check for missing and misconfigured expected routes
    for expected_route in EXPECTED_ROUTES:
        expected_path = expected_route['path']
        expected_methods = frozenset(expected_route['methods'])
        expected_handler = expected_route['handler_name']

        expected_key = (expected_path, expected_methods)

        if expected_key not in actual_routes_map:
            discrepancies['missing_routes'].append(expected_route)
        else:
            actual_handler = actual_routes_map[expected_key]
            if actual_handler != expected_handler:
                discrepancies['misconfigured_routes'].append({
                    'expected': expected_route,
                    'actual_handler': actual_handler
                })
            # Remove the matched route from the actual_routes_map
            # This helps identify any remaining routes as 'unexpected' later
            del actual_routes_map[expected_key]

    # Any remaining routes in actual_routes_map are considered unexpected
    for (path, methods_set), handler in actual_routes_map.items():
        discrepancies['unexpected_routes'].append({
            'path': path,
            'methods': sorted(list(methods_set)), # Convert frozenset back to sorted list for readability
            'handler_name': handler
        })

    return discrepancies

if __name__ == "__main__":
    # --- Setup for testing: Create a dummy app_router_registry.py ---
    # In a real scenario, app_router_registry.py would already exist.
    # This block creates a temporary file to simulate its presence for this script's execution.
    temp_dir = "temp_test_module"
    registry_file_name = "app_router_registry.py"
    registry_path = os.path.join(temp_dir, registry_file_name)

    # Content for the dummy app_router_registry.py
    # This content includes some routes that match EXPECTED_ROUTES,
    # one that is unexpected, and implicitly, some expected routes will be
    # missing or misconfigured based on the `EXPECTED_ROUTES` list above.
    dummy_registry_content = """
# app_router_registry.py
from flask import Blueprint, render_template, jsonify, request

# Create a Blueprint instance
app_router = Blueprint('app_router', __name__)

# --- UI Routes ---
@app_router.route('/dashboard', methods=['GET'])
def render_dashboard():
    return render_template('dashboard.html')

@app_router.route('/settings', methods=['GET'])
def render_settings():
    return render_template('settings.html')

# --- API Routes ---
@app_router.route('/api/status', methods=['GET'])
def get_status():
    return jsonify({"status": "ok"})

@app_router.route('/api/config', methods=['POST'])
def update_config():
    data = request.get_json()
    return jsonify({"message": "config updated", "data": data})

@app_router.route('/api/logs', methods=['GET'])
def get_logs(): # This handler name matches expected
    return jsonify({"logs": ["log1", "log2"]})

# An unexpected route (not in EXPECTED_ROUTES)
@app_router.route('/internal/health', methods=['GET'])
def get_health():
    return jsonify({"health": "good"})

# A route with a handler name that might be intentionally mismatched for testing
@app_router.route('/api/data', methods=['GET'])
def get_data_v1(): # EXPECTED_ROUTES might expect 'get_data_v2'
    return jsonify({"data": []})
"""

    try:
        os.makedirs(temp_dir, exist_ok=True)
        with open(registry_path, "w") as f:
            f.write(dummy_registry_content)

        # Add the temporary directory to sys.path so importlib can find the module
        sys.path.insert(0, temp_dir)

        # --- Execute the verification ---
        print("Starting app_router_registry wiring verification...")
        discrepancies = verify_wiring()

        # --- Report results ---
        if not any(discrepancies.values()): # Check if all lists in discrepancies are empty
            print("\nVERIFICATION PASSED: All expected routes are present and correctly configured.")
        else:
            print("\nVERIFICATION FAILED: Discrepancies found in app_router_registry wiring.")
            if discrepancies['missing_routes']:
                print("\n--- Missing Expected Routes ---")
                for route in discrepancies['missing_routes']:
                    if isinstance(route, str):
                        print(f"  - Error: {route}")
                    else:
                        print(f"  - Type: {route['type']}, Path: {route['path']}, Methods: {', '.join(route['methods'])}, Expected Handler: {route['handler_name']}")

            if discrepancies['misconfigured_routes']:
                print("\n--- Misconfigured Expected Routes ---")
                for route_info in discrepancies['misconfigured_routes']:
                    expected = route_info['expected']
                    print(f"  - Type: {expected['type']}, Path: {expected['path']}, Methods: {', '.join(expected['methods'])}, Expected Handler: {expected['handler_name']}, Actual Handler: {route_info['actual_handler']}")

            if discrepancies['unexpected_routes']:
                print("\n--- Unexpected Routes Found ---")
                for route_info in discrepancies['unexpected_routes']:
                    print(f"  - Path: {route_info['path']}, Methods: {', '.join(route_info['methods'])}, Handler: {route_info['handler_name']}")
            sys.exit(1) # Indicate failure

    finally:
        # --- Cleanup: Remove temporary files and directories ---
        if os.path.exists(registry_path):
            os.remove(registry_path)
        if os.path.exists(temp_dir):
            os.rmdir(temp_dir)
        if temp_dir in sys.path:
            sys.path.remove(temp_dir)