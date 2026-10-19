from typing import List, Set

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel


class ServiceEndpoint(BaseModel):
    """Machine‑readable description of a FastAPI endpoint."""
    path: str
    method: str
    summary: str | None = None
    authenticated: bool = False
    response_schema: str | None = None


router = APIRouter()


@router.get("/catalog", response_model=List[ServiceEndpoint])
def get_catalog() -> List[ServiceEndpoint]:
    """
    Introspect the running FastAPI application and return a list of
    ServiceEndpoint objects describing each registered route.
    """
    # Import the *real* FastAPI application instance.
    # The main application is defined in ``app.main`` in the repository.
    from app.main import app  # type: ignore

    endpoints: List[ServiceEndpoint] = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue

        # Determine authentication status.
        # A simple heuristic: if the route has any dependencies that are
        # callables named ``get_current_user`` (or similar), we treat it as
        # authenticated.  This avoids importing the actual security utilities.
        authenticated = any(
            dep.call is not None and dep.call.__name__ in {"get_current_user", "require_user"}
            for dep in route.dependant.dependencies
        )

        # Resolve the response model name, if any.
        response_schema = (
            route.response_model.__name__ if route.response_model is not None else None
        )

        # A route may support multiple HTTP methods; create an entry per method.
        for method in route.methods:
            # Skip automatic methods that FastAPI adds.
            if method in {"HEAD", "OPTIONS"}:
                continue

            endpoint = ServiceEndpoint(
                path=route.path,
                method=method,
                summary=route.summary,
                authenticated=authenticated,
                response_schema=response_schema,
            )
            endpoints.append(endpoint)

    return endpoints


# --------------------------------------------------------------------------- #
# Self‑test block
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    from fastapi.testclient import TestClient

    # Build a minimal FastAPI app that includes this router.
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    client = TestClient(test_app)

    resp = client.get("/api/catalog")
    if resp.status_code != 200:
        print(f"❌ Unexpected status code: {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    if not isinstance(data, list) or len(data) == 0:
        print("❌ Catalog returned empty list", file=sys.stderr)
        sys.exit(1)

    # Look for at least one entry that matches the required criteria.
    found = False
    for entry in data:
        if (
            isinstance(entry, dict)
            and entry.get("path", "").startswith("/api")
            and entry.get("method") == "GET"
            and entry.get("response_schema")
        ):
            found = True
            break

    if not found:
        print(
            "❌ No catalog entry satisfied the required conditions",
            file=sys.stderr,
        )
        sys.exit(1)

    print("PASS")