from fastapi import APIRouter, Depends
from app.db import get_session
from app.verdict_view_api import router as verdict_view_router
from app.dashboard_summary_api import router as dashboard_summary_router
from app.org_entity_search_api import router as org_entity_search_router
from app.app_scoring_consumer import router as app_scoring_consumer_router

router = APIRouter(dependencies=[Depends(get_session)])

router.include_router(
    verdict_view_router,
    prefix="/api",
    tags=["verdicts"],
)

router.include_router(
    dashboard_summary_router,
    prefix="/api",
    tags=["dashboard"],
)

router.include_router(
    org_entity_search_router,
    prefix="/api",
    tags=["org"],
)

router.include_router(
    app_scoring_consumer_router,
    prefix="/api",
    tags=["scoring"],
)