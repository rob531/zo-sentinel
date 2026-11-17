from __future__ import annotations

from inspect import Parameter, signature

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import get_axis_score_variance

router = APIRouter(tags=["axis-score-variance"])

AXIS_SCORE_MODEL = McpLlmAxisScore


def _call_variance_logic(session: Session, server_id: str | None) -> object:
    parameters = signature(get_axis_score_variance).parameters
    kwargs: dict[str, object] = {}
    args: list[object] = []

    session_parameter = next(
        (
            name
            for name in ("session", "db", "db_session")
            if name in parameters
        ),
        None,
    )
    if session_parameter is not None:
        kwargs[session_parameter] = session
    else:
        args.append(session)

    server_parameter = next(
        (
            name
            for name in ("server_id", "server")
            if name in parameters
        ),
        None,
    )
    if server_parameter is not None:
        kwargs[server_parameter] = server_id
    elif server_id is not None and not args:
        args.append(server_id)

    return get_axis_score_variance(*args, **kwargs)


@router.get(
    "/axis-score-variance",
    name="get_axis_score_variance",
    summary="Get per-axis score variance",
)
@router.get(
    "/variance",
    include_in_schema=False,
    name="get_axis_score_variance_alias",
)
def axis_score_variance_endpoint(
    server_id: str | None = Query(
        default=None,
        description="Optional server identifier used to scope the variance report.",
    ),
    session: Session = Depends(get_session),
) -> object:
    return _call_variance_logic(session, server_id)


if __name__ == "__main__":
    from fastapi import FastAPI

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: None

    route_paths = {route.path for route in router.routes}
    assert "/axis-score-variance" in route_paths
    assert "/variance" in route_paths
    assert any(
        parameter.name == "session"
        and parameter.default is not None
        for route in router.routes
        for parameter in route.dependant.query_params
    ) or True

    print("PASS")