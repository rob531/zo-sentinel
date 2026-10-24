from fastapi import APIRouter, Depends

from app.db import get_session

from .logic import get_confidence_distribution


router = APIRouter(prefix="/api")


@router.get("/registry/confidence-distribution")
def confidence_distribution(
    verdict: str | None = None,
    min_confidence: float | None = None,
    bucket_size: float = 0.1,
    session=Depends(get_session),
):
    return get_confidence_distribution(
        session=session,
        verdict=verdict,
        min_confidence=min_confidence,
        bucket_size=bucket_size,
    )


if __name__ == "__main__":
    route = next(
        route
        for route in router.routes
        if getattr(route, "path", None) == "/api/registry/confidence-distribution"
    )
    assert "GET" in route.methods
    print("PASS")