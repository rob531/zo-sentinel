#!/usr/bin/env python3
"""
populate_mcp_exemptions_initial_set.py

Initialises the *mcp_exemptions* table with a small, well‑known set of
exemptions.  The script is safe to run repeatedly – it will only insert
exemptions that are not already present.

The implementation talks directly to the management‑API exposed by
``mcp_exemptions_api.py`` (or, when that module is not available, falls back
to a raw ``requests`` call).  The API is expected to listen on the host
``localhost`` and on one of the ports defined in the product specification
(8780 or 8791).  If the environment variable ``MCP_API_PORT`` is set it
overrides the default.

Running the module as a script will populate the defaults and then perform a
light‑weight self‑test that the expected rows exist.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from typing import Any, Dict, List

import requests

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_HOST = "http://127.0.0.1"
DEFAULT_PORT = int(os.getenv("MCP_API_PORT", "8780"))
BASE_URL = f"{DEFAULT_HOST}:{DEFAULT_PORT}"

# The endpoint paths used by the management API (as described in the spec)
EXEMPTIONS_ENDPOINT = "/exemptions"


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def _log(msg: str) -> None:
    """Simple stdout logger – keeps the script quiet unless something goes
    wrong or we are in ``__main__``."""
    print(msg, file=sys.stderr)


def _api_url(path: str) -> str:
    """Return a fully‑qualified URL for the given API path."""
    return f"{BASE_URL}{path}"


def _get_existing_exemptions() -> List[Dict[str, Any]]:
    """Retrieve the current list of exemptions from the API."""
    try:
        resp = requests.get(_api_url(EXEMPTIONS_ENDPOINT), timeout=5)
        resp.raise_for_status()
        return resp.json()  # Expected to be a JSON list
    except Exception as exc:  # pragma: no cover – defensive, not expected in tests
        _log(f"Failed to fetch existing exemptions: {exc}")
        return []


def _exemption_match(existing: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    """
    Determine whether *candidate* is already represented by *existing*.

    The API stores a superset of the fields we provide.  For idempotency we
    consider an exemption a match when the following keys are equal:

    * ``server`` – the MCP server name (or ``None`` for a global exemption)
    * ``signal`` – the signal name (or ``None`` for a server‑wide exemption)
    * ``reason`` – human readable justification (optional)
    * ``expires_at`` – ISO‑8601 timestamp (date part is enough for our use‑case)

    If any of those keys differ we treat the entry as distinct.
    """
    keys = ("server", "signal", "reason", "expires_at")
    for k in keys:
        # ``expires_at`` may contain a time component – compare only the date part
        if k == "expires_at":
            existing_date = existing.get(k, "").split("T")[0]
            candidate_date = candidate.get(k, "").split("T")[0]
            if existing_date != candidate_date:
                return False
        else:
            if existing.get(k) != candidate.get(k):
                return False
    return True


def _post_exemption(payload: Dict[str, Any]) -> bool:
    """Create a new exemption via the API.  Returns ``True`` on success."""
    try:
        resp = requests.post(
            _api_url(EXEMPTIONS_ENDPOINT),
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # pragma: no cover – defensive
        _log(f"Failed to create exemption {payload!r}: {exc}")
        return False


# --------------------------------------------------------------------------- #
# Default exemption definitions
# --------------------------------------------------------------------------- #
def _default_exemptions() -> List[Dict[str, Any]]:
    """
    Return a list of dictionaries describing the exemptions that should be
    present after the first run.  The fields match the API contract:

    * ``server`` – name of the MCP server (or ``None`` for a global exemption)
    * ``signal`` – name of the signal (or ``None`` for a server‑wide exemption)
    * ``reason`` – free‑form text explaining why the exemption exists
    * ``expires_at`` – ISO‑8601 date (UTC) when the exemption should be removed
    """
    today = datetime.date.today()
    return [
        {
            "server": "test-server-1",
            "signal": None,
            "reason": "Migration – temporary exemption",
            "expires_at": (today + datetime.timedelta(days=30)).isoformat(),
        },
        {
            "server": "dev-mcp",
            "signal": "supply_chain",
            "reason": "Development environment – allow supply chain signal",
            "expires_at": (today + datetime.timedelta(days=90)).isoformat(),
        },
        {
            "server": None,
            "signal": "heartbeat",
            "reason": "Global heartbeat exemption for early rollout",
            "expires_at": (today + datetime.timedelta(days=60)).isoformat(),
        },
    ]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def populate_initial_exemptions() -> None:
    """
    Insert the default exemptions if they are not already present.

    The function is idempotent – running it multiple times will never create
    duplicate rows.
    """
    existing = _get_existing_exemptions()
    defaults = _default_exemptions()

    for candidate in defaults:
        # Skip if an equivalent exemption already exists
        if any(_exemption_match(e, candidate) for e in existing):
            _log(f"Exemption already present – skipping: {candidate}")
            continue

        # Create the exemption via the API
        if _post_exemption(candidate):
            _log(f"Created exemption: {candidate}")
        else:
            _log(f"Failed to create exemption (see earlier error): {candidate}")


# --------------------------------------------------------------------------- #
# Self‑test (executed when run as a script)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Populate the table
    populate_initial_exemptions()

    # Retrieve the current state for verification
    current = _get_existing_exemptions()
    expected = _default_exemptions()

    # Helper to locate a candidate in the current list
    def _found(candidate: Dict[str, Any]) -> bool:
        return any(_exemption_match(e, candidate) for e in current)

    # Verify that each default exemption is present
    missing = [c for c in expected if not _found(c)]
    if missing:
        _log(
            f"SELF‑TEST FAILED – the following expected exemptions are missing: {json.dumps(missing, indent=2)}"
        )
        sys.exit(1)

    # Verify that we have *at least* the expected number of rows (there may be
    # other exemptions that were added manually)
    if len(current) < len(expected):
        _log(
            f"SELF‑TEST FAILED – expected at least {len(expected)} exemptions, got {len(current)}"
        )
        sys.exit(1)

    _log(
        f"SELF‑TEST PASSED – {len(expected)} default exemptions are present (total rows: {len(current)})"
    )
    sys.exit(0)