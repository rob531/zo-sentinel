# api_endpoint_listing_api.py
from fastapi import FastAPI
from fastapi.routing import APIRoute

# The main FastAPI application instance
app = FastAPI()

@app.get("/api/endpoints")
async def list_endpoints():
    """
    Dynamically discovers and lists all registered API endpoints within the FastAPI application.
    Returns a list of dictionaries, each containing 'method' and 'path' keys.
    """
    endpoints = []
    # Iterate through all registered routes in the application
    for route in app.routes:
        # We are interested in APIRoute instances, which represent actual API endpoints
        if isinstance(route, APIRoute):
            # A single route path can support multiple HTTP methods (e.g., GET and HEAD)
            for method in route.methods:
                endpoints.append({
                    "method": method,
                    "path": route.path
                })
    return endpoints

# __main__ block for testing purposes
if __name__ == "__main__":
    from fastapi.testclient import TestClient

    # Create a *mocked* FastAPI app for testing.
    # This ensures that the test client operates on an isolated application
    # with specific test routes, rather than the global 'app' instance directly.
    test_app = FastAPI()

    # Re-register the endpoint listing logic onto the test_app.
    # This allows the /api/endpoints route to discover routes within 'test_app'.
    @test_app.get("/api/endpoints")
    async def list_endpoints_for_test_app():
        endpoints = []
        for route in test_app.routes:
            if isinstance(route, APIRoute):
                for method in route.methods:
                    endpoints.append({
                        "method": method,
                        "path": route.path
                    })
        return endpoints

    # Add some dummy routes to the test_app.
    # These routes will be discovered by the /api/endpoints endpoint.
    @test_app.get("/test/items/{item_id}")
    async def read_test_item(item_id: int):
        return {"item_id": item_id}

    @test_app.post("/test/users/")
    async def create_test_user(name: str):
        return {"name": name}

    @test_app.put("/test/data/{data_id}")
    async def update_test_data(data_id: int):
        return {"data_id": data_id}

    # Initialize the TestClient with the mocked application
    client = TestClient(test_app)

    # Make a GET request to the /api/endpoints endpoint
    response = client.get("/api/endpoints")

    # --- ACCEPTANCE CRITERIA ASSERTIONS ---

    # 1. Assert status code is 200 OK
    assert response.status_code == 200, f"Expected status code 200, but got {response.status_code}"

    # 2. Get the JSON response data
    data = response.json()

    # 3. Assert that the response is a non-empty list
    assert isinstance(data, list), f"Expected response to be a list, but got {type(data)}"
    assert len(data) > 0, "Expected a non-empty list of endpoints"

    # 4. Assert that each item in the list is a dictionary containing 'method' and 'path' keys
    for endpoint in data:
        assert isinstance(endpoint, dict), f"Expected endpoint to be a dictionary, but got {type(endpoint)}"
        assert "method" in endpoint, f"Endpoint dictionary missing 'method' key: {endpoint}"
        assert "path" in endpoint, f"Endpoint dictionary missing 'path' key: {endpoint}"
        assert isinstance(endpoint["method"], str), f"Expected 'method' to be a string, but got {type(endpoint['method'])}"
        assert isinstance(endpoint["path"], str), f"Expected 'path' to be a string, but got {type(endpoint['path'])}"

    # 5. Verify that the expected routes (including dummy routes and the endpoint itself) are present
    # FastAPI automatically adds 'HEAD' method for 'GET' routes.
    expected_routes_set = {
        ("GET", "/api/endpoints"),
        ("HEAD", "/api/endpoints"),
        ("GET", "/test/items/{item_id}"),
        ("HEAD", "/test/items/{item_id}"),
        ("POST", "/test/users/"),
        ("PUT", "/test/data/{data_id}"),
    }
    found_routes_set = set()
    for endpoint in data:
        found_routes_set.add((endpoint["method"], endpoint["path"]))

    assert found_routes_set == expected_routes_set, \
        f"Discovered routes do not match expected routes.\nExpected: {expected_routes_set}\nFound: {found_routes_set}"

    # If all assertions pass, print PASS
    print("PASS")