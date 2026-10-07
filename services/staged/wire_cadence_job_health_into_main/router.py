from fastapi import APIRouter, Depends
from app.db import get_session
from services.staged.cadence_job_health_api.router import router as cadence_job_health_router

router = APIRouter()
router.include_router(cadence_job_health_router, dependencies=[Depends(get_session)])