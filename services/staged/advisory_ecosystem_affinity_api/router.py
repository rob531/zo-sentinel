from inspect import signature
from typing import Any, Dict

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink
from .logic import ecosystem_affinity

router = APIRouter(prefix="/api", tags=["vulnerability"])


@router.get("/vuln/ecosystem-affinity")
def get_ecosystem_affinity(
    limit: int = Query(25, description="Maximum number of package results to return"),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    bounded_limit = max(1, min(200, limit))
    parameters = signature(ecosystem_affinity).parameters
    kwargs: Dict[str, Any] = {"limit": bounded_limit}

    if "session" in parameters:
        kwargs["session"] = session
    elif "db" in parameters:
        kwargs["db"] = session

    return ecosystem_affinity(**kwargs)


ecosystem_affinity_endpoint = get_ecosystem_affinity


if __name__ == "__main__":
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: None

    assert any(
        getattr(route, "path", None) == "/api/vuln/ecosystem-affinity"
        for route in test_app.routes
    )
    print("PASS")