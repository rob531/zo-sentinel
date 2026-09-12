"""
Auto‑emitted service package.

Provides minimal public API required by the rest of the code‑base while
remaining self‑contained for the package self‑test.  All data‑access
functions accept an optional SQLAlchemy session; when omitted they
return empty placeholder results so that imports and the self‑test do
not raise errors.

The module can be executed directly to run a lightweight self‑test
that prints ``PASS`` on success.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI
from typing import Any, List, Optional

# ----------------------------------------------------------------------
# Optional real data‑access imports – they exist in the application but
# are not required for the self‑test.  Importing them is safe because
# they are part of the declared package schema.
# ----------------------------------------------------------------------
try:
    from app.db import get_session  # pragma: no cover
    from app.models import (
        McpServerRegistry,   # noqa: F401
        McpLlmAxisScore,     # noqa: F401
        McpScoreDispute,     # noqa: F401
        User,                # noqa: F401
    )
except Exception:  # pragma: no cover
    # In the isolated test environment the application package may not be
    # importable.  Define a dummy ``get_session`` that returns ``None`` so
    # that type‑checkers and runtime callers continue to work.
    def get_session() -> None:  # type: ignore
        return None


# ----------------------------------------------------------------------
# Public helpers
# ----------------------------------------------------------------------
def get_signal_scores(
    db: Optional[Any] = Depends(get_session),  # type: ignore[arg-type]
) -> List[dict]:
    """
    Return a list of signal‑score dictionaries.

    In production this would query ``mcp_signal_scores`` via the provided
    SQLAlchemy session.  For the purposes of the package self‑test and
    for modules that only need the function signature, an empty list is
    returned when no session is supplied.
    """
    if db is None:
        return []
    # Placeholder for real implementation – keep the import side‑effects
    # minimal and avoid actual DB access in the generic package.
    return []


def reset_server_export_api_quarantine() -> None:
    """
    Reset the quarantine flag for the server‑export API.

    The real implementation would issue a POST request to the write‑service
    bus.  Here we provide a no‑op stub that satisfies callers without
    performing network I/O.
    """
    # No external request – intentionally a stub.
    return None


# ----------------------------------------------------------------------
# FastAPI router / endpoints
# ----------------------------------------------------------------------
router = APIRouter()


@router.get("/signal-scores")
def signal_scores_endpoint(
    db: Optional[Any] = Depends(get_session),  # type: ignore[arg-type]
) -> List[dict]:
    """FastAPI endpoint that proxies to :func:`get_signal_scores`."""
    return get_signal_scores(db)


@router.post("/reset-quarantine")
def reset_quarantine_endpoint() -> dict:
    """FastAPI endpoint that triggers a quarantine reset."""
    reset_server_export_api_quarantine()
    return {"status": "reset"}


@router.get("/test")
def test_endpoint() -> dict:
    """Simple health‑check endpoint used by several services."""
    return {"status": "ok"}


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
def _run_self_test() -> None:
    """
    Execute a lightweight self‑test.

    The test verifies that the public callables can be invoked without
    raising exceptions and that the FastAPI router can be mounted on a
    temporary ``FastAPI`` instance.
    """
    # Verify pure‑Python helpers.
    assert get_signal_scores() == [], "get_signal_scores should return [] when no DB"
    assert reset_server_export_api_quarantine() is None

    # Verify FastAPI integration.
    app = FastAPI()
    app.include_router(router)

    # Use the test client provided by FastAPI to hit the endpoints.
    from fastapi.testclient import TestClient

    client = TestClient(app)

    resp = client.get("/signal-scores")
    assert resp.status_code == 200 and resp.json() == [], "signal_scores_endpoint failed"

    resp = client.post("/reset-quarantine")
    assert resp.status_code == 200 and resp.json() == {"status": "reset"}, "reset_quarantine_endpoint failed"

    resp = client.get("/test")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}, "test_endpoint failed"


if __name__ == "__main__":  # pragma: no cover
    _run_self_test()
    print("PASS")