from fastapi import APIRouter, Depends
import inspect

from app.db import get_session
from .logic import run, heartbeat

router = APIRouter(
    prefix="/axis_to_risk_tier_consumer",
    tags=["axis_to_risk_tier_consumer"],
)


@router.get("/run", name="axis_to_risk_tier_consumer:run")
async def run_endpoint(session=Depends(get_session)):
    """Execute a single daemon iteration."""
    result = run(session)
    if inspect.isawaitable(result):
        result = await result
    return result


@router.get("/heartbeat", name="axis_to_risk_tier_consumer:heartbeat")
async def heartbeat_endpoint(session=Depends(get_session)):
    """Return health information for the daemon."""
    result = heartbeat(session)
    if inspect.isawaitable(result):
        result = await result
    return result