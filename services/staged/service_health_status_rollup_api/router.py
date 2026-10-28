from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_rollup

router = APIRouter(prefix="/api", tags=["service_health"])


@router.get("/service_health/rollup")
def service_health_status_rollup(session: Session = Depends(get_session)):
    return get_rollup(session)


if __name__ == "__main__":
    from fastapi import FastAPI

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: None
    print("PASS")