import sys
import json
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Import the FastAPI app and the DB session dependency from the production code.
# The module must expose a FastAPI instance named `app`.
from scorecard_badge_api import app  # noqa: F401
from app.db import get_session
import app.models as models  # noqa: F401


def _override_session():
    """
    Create an in‑memory SQLite session and bind all production models to it.
    This override is only used for the self‑test; the module itself continues
    to import `get_session` from `app.db`.
    """
    engine = create_engine("sqlite:///:memory:", echo=False)
    # Create all tables defined in the production models.
    if hasattr(models, "Base"):
        models.Base.metadata.create_all(bind=engine)
    else:
        # Fallback: iterate over attributes looking for SQLAlchemy declarative bases.
        for attr in dir(models):
            obj = getattr(models, attr)
            if hasattr(obj, "metadata"):
                obj.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _find_badge_endpoint(client: TestClient) -> str:
    """
    Scan the FastAPI app routes for a path that contains the word 'badge'.
    Return the first matching path (including any path parameters as placeholders).
    """
    for route in client.app.routes:
        if hasattr(route, "path") and "badge" in route.path:
            # Prefer GET methods for simplicity.
            if "GET" in getattr(route, "methods", []):
                return route.path
    raise RuntimeError("No badge endpoint found in the FastAPI app.")


def _assert_iso8601(value: str) -> None:
    """
    Validate that a string is a valid ISO‑8601 timestamp.
    """
    try:
        # datetime.fromisoformat supports most ISO‑8601 formats in Python 3.7+.
        datetime.fromisoformat(value)
    except Exception as exc:
        raise AssertionError(f"Timestamp '{value}' is not ISO‑8601: {exc}") from exc


def run_self_test() -> None:
    """
    Execute the integration smoke test.
    """
    # Override the DB dependency with an in‑memory SQLite session.
    test_session = _override_session()
    app.dependency_overrides[get_session] = lambda: test_session

    client = TestClient(app)

    try:
        endpoint = _find_badge_endpoint(client)
        response = client.get(endpoint)
        assert response.status_code == 200, f"Unexpected status {response.status_code}"
        payload = response.json()
        assert isinstance(payload, dict), "Response JSON is not an object"
        assert "risk_tier" in payload, "Missing 'risk_tier' in response"

        # If a timestamp field exists, validate its format.
        for key in ("timestamp", "created_at", "updated_at"):
            if key in payload:
                _assert_iso8601(payload[key])
                break  # only need to validate one timestamp field if present

        print("PASS")
    finally:
        # Clean up the dependency override to avoid side effects.
        app.dependency_overrides.pop(get_session, None)


if __name__ == "__main__":
    try:
        run_self_test()
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)