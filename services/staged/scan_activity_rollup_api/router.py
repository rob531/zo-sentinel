from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_scan_activity_rollup as fetch_scan_activity_rollup

router = APIRouter()


@router.get("/scan-activity-rollup")
def get_scan_activity_rollup(session: Session = Depends(get_session)):
    return fetch_scan_activity_rollup(session)


if __name__ == "__main__":
    from fastapi import FastAPI

    that_app = FastAPI()
    that_app.include_router(router)
    assert "/scan-activity-rollup" in {route.path for route in that_app.routes}
    print("PASS")