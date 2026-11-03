from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_axis_completeness


router = APIRouter(prefix="/api")


class AxisCompletenessBucket(BaseModel):
    axis_count: int = Field(ge=0, le=7)
    servers: int = Field(ge=0)


class AxisCompletenessServer(BaseModel):
    server_id: str
    name: str | None = None
    verdict: str | None = None
    risk_tier: str | None = None
    trust_score: float | None = None
    axis_count: int = Field(ge=0, le=7)
    missing_axes: list[str] = Field(default_factory=list)
    last_scored_at: str | None = None


class AxisCompletenessResponse(BaseModel):
    total_servers: int = Field(ge=0)
    complete: int = Field(ge=0)
    incomplete: int = Field(ge=0)
    buckets: list[AxisCompletenessBucket] = Field(default_factory=list)
    servers: list[AxisCompletenessServer] = Field(default_factory=list)


@router.get(
    "/servers/axis-completeness",
    response_model=AxisCompletenessResponse,
)
def axis_completeness(
    limit: int = Query(default=50, ge=1, le=500),
    min_axes: int = Query(default=0, ge=0, le=7),
    session: Session = Depends(get_session),
) -> AxisCompletenessResponse:
    result = get_axis_completeness(session, limit, min_axes)
    return AxisCompletenessResponse.model_validate(result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as OrmSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine, checkfirst=True)
    McpLlmAxisScore.__table__.create(test_engine, checkfirst=True)

    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestSession() as session:
        session.add(
            McpServerRegistry(
                server_id="server-zero",
                name="Zero-score server",
                verdict="unknown",
                risk_tier="unknown",
                trust_score=0.0,
            )
        )
        session.commit()

    response = TestClient(test_app).get(
        "/api/servers/axis-completeness",
        params={"limit": 1, "min_axes": 0},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["total_servers"] == 1
    assert payload["complete"] == 0
    assert payload["incomplete"] == 1
    assert len(payload["servers"]) == 1
    assert payload["servers"][0]["axis_count"] == 0
    assert len(payload["servers"][0]["missing_axes"]) == 7
    print("PASS")