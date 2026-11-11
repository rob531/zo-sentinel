from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_api_key_org_activity

router = APIRouter()


@router.get("/api/api-keys/org-activity")
def get_api_key_org_activity_endpoint(session: Session = Depends(get_session)):
    return get_api_key_org_activity(session)


def _self_test() -> None:
    route = next(
        route
        for route in router.routes
        if getattr(route, "path", None) == "/api/api-keys/org-activity"
    )
    assert "GET" in route.methods
    assert route.methods is not None
    assert "GET" in route.methods
    print("PASS")


if __name__ == "__main__":
    _self_test()