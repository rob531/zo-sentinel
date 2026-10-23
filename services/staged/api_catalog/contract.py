"""API Catalog Service - Introspects FastAPI app routes and returns endpoint manifest."""

from typing import List
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel


class ServiceEndpoint(BaseModel):
    """Represents a single API endpoint in the service catalog."""
    path: str
    method: str
    summary: str
    authenticated: bool
    response_schema: str


def create_app(app: FastAPI) -> FastAPI:
    """Create the API catalog service that introspects the provided app."""
    
    api = FastAPI()
    
    @api.get("/api/catalog", response_model=List[ServiceEndpoint])
    def get_catalog() -> List[ServiceEndpoint]:
        """
        Introspect the mounted app and return a manifest of all live REST endpoints.
        
        Returns:
            List of ServiceEndpoint objects describing each route.
        """
        endpoints: List[ServiceEndpoint] = []
        
        for route in app.routes:
            if isinstance(route, APIRoute):
                # Get the primary HTTP method (exclude HEAD, OPTIONS)
                methods = route.methods - {"HEAD", "OPTIONS"}
                for method in methods:
                    response_schema = ""
                    if route.response_model:
                        response_schema = route.response_model.__name__
                    
                    # Determine if endpoint requires auth by checking for auth dependencies
                    authenticated = False
                    for dependency in route.dependant.dependencies:
                        if dependency.name in ("current_user", "user", "token", "auth", "credentials"):
                            authenticated = True
                            break
                    
                    endpoint = ServiceEndpoint(
                        path=route.path,
                        method=method,
                        summary=route.summary or route.name or "",
                        authenticated=authenticated,
                        response_schema=response_schema,
                    )
                    endpoints.append(endpoint)
        
        return endpoints
    
    return api


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    
    # Create a sample app for self-test
    sample_app = FastAPI(title="Sample Sentinel App")
    
    @sample_app.get("/api", response_model=dict)
    def read_root():
        """Root API endpoint."""
        return {"status": "ok"}
    
    @sample_app.get("/api/items/{item_id}")
    def get_item(item_id: int):
        """Get an item by ID."""
        return {"item_id": item_id}
    
    @sample_app.post("/api/items")
    def create_item():
        """Create a new item."""
        return {"created": True}
    
    # Mount the catalog service
    app = create_app(sample_app)
    client = TestClient(app)
    
    response = client.get("/api/catalog")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert isinstance(data, list), f"Expected list, got {type(data)}"
    assert len(data) > 0, "Expected non-empty list of endpoints"
    
    # Find the /api endpoint
    api_endpoint = None
    for endpoint in data:
        if endpoint["path"] == "/api" and endpoint["method"] == "GET":
            api_endpoint = endpoint
            break
    
    assert api_endpoint is not None, "Expected to find /api GET endpoint"
    assert api_endpoint["response_schema"], "Expected non-empty response_schema"
    
    print("PASS")