from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import HighValueRouter

router = APIRouter(prefix="/api", tags=["wire_high_value_routers_into_main"])

@router.get("/routers/")
async def list_high_value_routers(session: Session = Depends(get_session)):
    """List all high-value routers."""
    routers = session.query(HighValueRouter).all()
    return routers

@router.get("/routers/{router_id}")
async def wire_router(router_id: int, session: Session = Depends(get_session)):
    """Wire a specific high-value router by ID."""
    router = session.query(HighValueRouter).filter(HighValueRouter.id == router_id).first()
    if router is None:
        raise HTTPException(status_code=404, detail="Router not found")
    return router
