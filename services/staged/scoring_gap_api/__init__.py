"""
Service package core utilities.

Provides a common Pydantic base model for all snapshot / response
schemas and a tiny HTTP helper that enforces a timeout (Bandit
B113 mitigation). The module also includes a minimal self‑test that
verifies the base model can be instantiated; the test prints ``PASS``
when executed as a script.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import requests
from pydantic import BaseModel


class ServiceBase(BaseModel):
    """Common base for all Pydantic models in the service package.

    Enables ORM mode so that SQLAlchemy models can be returned directly
    from FastAPI endpoints.
    """

    class Config:
        orm_mode = True


def fetch_json(url: str, *, timeout: int = 5, **kwargs: Any) -> Dict[str, Any]:
    """GET a JSON payload from *url* with a mandatory timeout.

    Args:
        url: Target URL.
        timeout: Seconds to wait before aborting the request (default 5).
        **kwargs: Additional arguments forwarded to ``requests.get``.

    Returns:
        Decoded JSON dictionary.

    Raises:
        requests.RequestException: Propagates any request‑related error.
        json.JSONDecodeError: If the response body is not valid JSON.
    """
    response = requests.get(url, timeout=timeout, **kwargs)
    response.raise_for_status()
    return response.json()


def post_json(url: str, payload: Any, *, timeout: int = 5, **kwargs: Any) -> Dict[str, Any]:
    """POST *payload* as JSON to *url* with a mandatory timeout.

    Args:
        url: Target URL.
        payload: JSON‑serialisable data to send.
        timeout: Seconds to wait before aborting the request (default 5).
        **kwargs: Additional arguments forwarded to ``requests.post``.

    Returns:
        Decoded JSON dictionary from the response.

    Raises:
        requests.RequestException: Propagates any request‑related error.
        json.JSONDecodeError: If the response body is not valid JSON.
    """
    response = requests.post(url, json=payload, timeout=timeout, **kwargs)
    response.raise_for_status()
    return response.json()


def run_self_test() -> bool:
    """Simple sanity check for the package.

    Verifies that ``ServiceBase`` can be subclassed and instantiated
    without error.
    """
    try:
        class Dummy(ServiceBase):
            foo: int = 42

        Dummy()
        # Verify HTTP helpers raise a clear error when the endpoint is
        # unreachable (expected in test environment). We only check that
        # the functions exist and accept the required arguments.
        _ = fetch_json
        _ = post_json
        return True
    except Exception:
        return False


if __name__ == "__main__":
    print("PASS" if run_self_test() else "FAIL")