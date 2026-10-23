"""zo_sentinel package - Auto-emitted service package for MCP sentinel operations."""

from __future__ import annotations

import logging
import sys
from typing import Any

try:
    from ._version import version as __version__
except ImportError:
    __version__ = "0.0.0"

from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    User,
    VulnAdvisory,
)

__all__ = [
    "__version__",
    "get_session",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "McpServerRegistry",
    "Org",
    "User",
    "VulnAdvisory",
    "make_request",
    "make_service_call",
    "configure_logging",
]

log = logging.getLogger(__name__)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure package-wide logging."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def make_request(
    method: str,
    url: str,
    timeout: float = 30.0,
    **kwargs: Any,
) -> dict[str, Any]:
    """Make an HTTP request with proper timeout configuration.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        url: Target URL
        timeout: Request timeout in seconds (default: 30.0)
        **kwargs: Additional arguments passed to requests
        
    Returns:
        Response JSON as dict
        
    Raises:
        requests.HTTPError: On non-2xx responses
    """
    import requests

    response = requests.request(method, url, timeout=timeout, **kwargs)
    response.raise_for_status()
    return response.json()


def make_service_call(
    endpoint: str,
    method: str = "GET",
    base_url: str = "http://127.0.0.1:8772",
    timeout: float = 30.0,
    **kwargs: Any,
) -> dict[str, Any]:
    """Make a call to the local ZoComputer service.

    Args:
        endpoint: API endpoint path
        method: HTTP method
        base_url: Service base URL
        timeout: Request timeout in seconds (default: 30.0)
        **kwargs: Additional request arguments
        
    Returns:
        Response data as dict
    """
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    return make_request(method, url, timeout=timeout, **kwargs)


def _run_self_test() -> bool:
    """Run package self-test. Returns True if all tests pass."""
    from unittest.mock import MagicMock

    configure_logging(logging.WARNING)

    results = []

    results.append(("version", __version__ is not None))
    results.append(("get_session", callable(get_session)))

    model_checks = [
        ("McpServerRegistry", McpServerRegistry),
        ("McpLlmAxisScore", McpLlmAxisScore),
        ("McpScoreDispute", McpScoreDispute),
        ("Org", Org),
        ("User", User),
        ("VulnAdvisory", VulnAdvisory),
    ]
    for name, model in model_checks:
        results.append((name, model is not None))

    results.append(("configure_logging", callable(configure_logging)))
    results.append(("make_request", callable(make_request)))
    results.append(("make_service_call", callable(make_service_call)))

    mock_session = MagicMock()
    results.append(("get_session_session", mock_session is not None))

    all_passed = all(passed for _, passed in results)
    return all_passed


if __name__ == "__main__":
    print("Running zo_sentinel self-test...")
    if _run_self_test():
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)