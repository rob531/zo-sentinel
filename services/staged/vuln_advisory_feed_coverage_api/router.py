from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_feed_coverage

router = APIRouter()


@router.get("/api/vuln/advisories/feed-coverage")
def feed_coverage(session: Session = Depends(get_session)):
    """Return advisory intake coverage grouped by source feed and severity."""
    return get_feed_coverage(session)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: None

    routes = {
        route.path: route
        for route in test_app.routes
        if hasattr(route, "path")
    }
    assert "/api/vuln/advisories/feed-coverage" in routes
    assert "GET" in routes["/api/vuln/advisories/feed-coverage"].methods

    print("PASS")