from fastapi import APIRouter, Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

router = APIRouter()

@router.get("/risk-tier")
async def get_risk_tier():
    """
    Get risk tier information
    """
    return {"message": "Risk tier service endpoint"}

@router.post("/risk-tier")
async def update_risk_tier():
    """
    Update risk tier information
    """
    return {"message": "Risk tier updated successfully"}