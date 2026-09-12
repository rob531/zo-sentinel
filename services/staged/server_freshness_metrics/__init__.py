"""
Auto‑emitted service package utilities.

Provides a minimal, self‑contained API that satisfies intra‑service imports
used throughout the quarantine code‑base.  All functions are stubs that
return simple, deterministic data so that the package can be imported and
its __main__ self‑test can run without external dependencies.
"""

from __future__ import annotations

from typing import Any, List, Dict

# --------------------------------------------------------------------------- #
# Public helpers – stubs that other modules import and call.
# --------------------------------------------------------------------------- #

def get_signal_scores(session: Any = None) -> List[Dict[str, Any]]:
    """
    Return a placeholder list of signal‑score dictionaries.

    The real implementation would query ``mcp_signal_scores`` via the
    write‑service bus.  Here we return an empty list to keep the module
    importable without external services.
    """
    return []


def _run_self_test(session: Any = None) -> bool:
    """
    Perform a very light‑weight self‑test.

    Returns ``True`` to indicate success.  The package’s ``__main__`` block
    uses this to verify that the stub works.
    """
    return True


def reset_server_export_api_quarantine(session: Any = None) -> None:
    """
    Placeholder for the quarantine‑reset routine.

    In production this would trigger a reset of the export API; the stub
    simply does nothing.
    """
    return None


def signal_scores_endpoint(session: Any = None) -> Dict[str, Any]:
    """
    FastAPI‑style endpoint returning signal scores.

    Returns a JSON‑serialisable mapping with an empty ``scores`` list.
    """
    return {"scores": get_signal_scores(session)}


def reset_server_export_api_quarantine_endpoint(session: Any = None) -> Dict[str, str]:
    """
    FastAPI‑style endpoint that acknowledges a quarantine reset request.
    """
    reset_server_export_api_quarantine(session)
    return {"status": "reset initiated"}


def test_endpoint(session: Any = None) -> Dict[str, str]:
    """
    Generic test endpoint used by several services.
    """
    return {"result": "ok"}


def users_endpoint(session: Any = None) -> Dict[str, List[Dict[str, Any]]]:
    """
    Endpoint that would normally return a list of users.
    """
    return {"users": []}


def users_list(session: Any = None) -> List[Dict[str, Any]]:
    """
    Helper returning a list of user records.
    """
    return []


# --------------------------------------------------------------------------- #
# Stub model classes – used as base classes elsewhere.
# --------------------------------------------------------------------------- #

class McpLlmAxisScoreRead:
    """
    Base class for LLM axis score read models.
    """
    pass


class Users:
    """
    Base class for user models.
    """
    pass


# --------------------------------------------------------------------------- #
# __main__ self‑test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # Run a minimal suite of checks to ensure the stubs behave as expected.
    assert isinstance(get_signal_scores(), list)
    assert _run_self_test() is True
    assert isinstance(signal_scores_endpoint(), dict)
    assert isinstance(reset_server_export_api_quarantine_endpoint(), dict)
    assert isinstance(test_endpoint(), dict)
    assert isinstance(users_endpoint(), dict)
    assert isinstance(users_list(), list)

    print("PASS")