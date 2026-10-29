from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_indicator_type_mix

router = APIRouter(prefix="/api", tags=["threat-intel"])


@router.get("/threat-intel/indicator-type-mix")
def get_threat_intel_indicator_type_mix(
    source: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    return get_indicator_type_mix(session=session, source=source)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    route = next(
        route
        for route in test_app.routes
        if getattr(route, "path", None)
        == "/api/threat-intel/indicator-type-mix"
    )
    assert route.methods is not None
    assert "GET" in route.methods
    print("PASS")