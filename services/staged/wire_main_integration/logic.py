"""
services/staged/wire_main_integration/logic.py

Wires the high‑value FastAPI routers into the main FastAPI application.
This mirrors the pattern used in `services/_exemplar/logic.py` and
relies on the real router objects defined in the `app` package.
"""

from fastapi import FastAPI

# Import the real routers from the application package.
# Each module is expected to expose a FastAPI `router` instance.
from app.verdict_view_api import router as verdict_router
from app.dashboard_summary_api import router as dashboard_router
from app.org_entity_search_api import router as org_router
from app.app_scoring_consumer import router as scoring_router


def wire_high_value_routers_into_main(app: FastAPI) -> None:
    """
    Attach the high‑value routers to the provided FastAPI `app`.

    The prefixes are chosen so that the routes are discoverable by the
    acceptance test (e.g., `/verdict` and `/dashboard` must appear in the
    application's route list).
    """
    # Verdict view API
    app.include_router(
        verdict_router,
        prefix="/verdict",
        tags=["verdicts"],
    )

    # Dashboard summary API
    app.include_router(
        dashboard_router,
        prefix="/dashboard",
        tags=["dashboard"],
    )

    # Organization entity search API
    app.include_router(
        org_router,
        prefix="/org",
        tags=["organization"],
    )

    # Application scoring consumer API
    app.include_router(
        scoring_router,
        prefix="/scoring",
        tags=["scoring"],
    )