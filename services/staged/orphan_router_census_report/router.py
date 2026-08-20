"""
Orphan Router Census Report Service

Scans for unmounted FastAPI routers and reports them.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

from .logic import get_orphan_router_census

router = APIRouter(prefix="/api/internal", tags=["orphan-router-census"])


@router.get("/orphan-router-census")
async def get_orphan_census_report(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Scans app/ and services/active/ for FastAPI routers that are not mounted
    in app_router_registry.py and returns a report of orphan routes.
    
    Returns:
        {
            "orphans": [
                {
                    "file": "path/to/router.py",
                    "routes": [
                        {"path": "/example", "method": "GET"}
                    ]
                }
            ],
            "total_unmounted_routes": int,
            "scanned_files": int
        }
    """
    return await get_orphan_router_census(session)