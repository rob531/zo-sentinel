from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_axis_score_age_histogram

router = APIRouter()


@router.get("/api/axis/score-age-histogram")
def axis_score_age_histogram(
    axis_name: str | None = Query(default=None),
    days: int = Query(...),
    session: Session = Depends(get_session),
) -> dict:
    return get_axis_score_age_histogram(
        session=session,
        axis_name=axis_name,
        days=days,
    )


if __name__ == "__main__":
    from fastapi import FastAPI

    that_app = FastAPI()
    that_app.include_router(router)

    route = next(
        route
        for route in that_app.routes
        if getattr(route, "path", None) == "/api/axis/score-age-histogram"
    )
    assert "GET" in route.methods
    print("PASS")