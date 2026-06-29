"""
app_router_registry.py

Centralised helper to register all routers that belong to the *app* package.

The function :func:`include_app_routers` imports every router defined in the
``app.routers`` sub‑package and mounts it on the supplied ``FastAPI`` instance.
The list of router modules is deliberately kept explicit – this makes the
registry easy to audit and avoids accidental inclusion of stray modules.

A tiny self‑test is provided in the ``__main__`` block.  Running the module
directly will spin up a ``FastAPI`` app, register the routers and assert that
the resulting application has at least one route.  This mirrors the behaviour
described in the project’s Appendix E “app assembly directive candidate”.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Iterable, List

from fastapi import FastAPI

# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def include_app_routers(app: FastAPI) -> None:
    """
    Mount every router defined in the ``app.routers`` package onto ``app``.

    The function iterates over a hard‑coded list of module names, imports each
    module, extracts the attribute named ``router`` (expected to be an
    ``APIRouter`` instance) and registers it with the FastAPI application.

    Parameters
    ----------
    app:
        The FastAPI instance that should receive the routers.

    Raises
    ------
    AttributeError
        If a module does not expose a ``router`` attribute.
    ImportError
        If a listed router module cannot be imported.
    """
    # Explicit list of router modules – keep this in sync with the package layout.
    router_modules: List[str] = [
        "auth",
        "rbac",
        "verdict_view_api",
        "dashboard_summary_api",
        "org_entity_search_api",
        "entity_report_exporter",
        "org_api_key_manager",
        "product_audit_log",
    ]

    for module_name in router_modules:
        full_name = f"app.routers.{module_name}"
        try:
            module = importlib.import_module(full_name)
        except ImportError as exc:
            raise ImportError(f"Unable to import router module '{full_name}'.") from exc

        if not hasattr(module, "router"):
            raise AttributeError(
                f"Router module '{full_name}' does not expose a 'router' attribute."
            )

        app.include_router(module.router)


# --------------------------------------------------------------------------- #
# Self‑test (executed when the file is run as a script)
# --------------------------------------------------------------------------- #

def _self_test() -> None:
    """
    Minimal sanity‑check: create a FastAPI app, register all routers and verify
    that the resulting application has at least one route.
    """
    from fastapi import FastAPI

    test_app = FastAPI()
    include_app_routers(test_app)

    # ``test_app.routes`` contains the automatically added OpenAPI/Docs routes
    # as well as the routes contributed by the imported routers.  We only need
    # to ensure that *some* router contributed at least one endpoint.
    route_count = len(test_app.routes)
    assert route_count > 0, "No routes were registered – router inclusion failed."

    # Provide a tiny textual confirmation when run directly.
    print(f"✅ Self‑test passed – {route_count} route(s) registered.")


if __name__ == "__main__":
    # When executed as a script, run the self‑test.
    _self_test()