from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_url_scheme_distribution

router = APIRouter(prefix="/api")


class UrlSchemeBucket(BaseModel):
    scheme: str
    count: int
    pct: float


class UrlSchemeDistributionResponse(BaseModel):
    total: int
    buckets: List[UrlSchemeBucket]


@router.get(
    "/registry/url-schemes",
    response_model=UrlSchemeDistributionResponse,
)
def registry_url_scheme_distribution(
    session: Session = Depends(get_session),
    registry_source: Optional[str] = Query(default=None),
) -> UrlSchemeDistributionResponse:
    return get_url_scheme_distribution(
        session=session,
        registry_source=registry_source,
    )


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)

    with Session(engine) as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="scheme-https-1",
                    name="https-1",
                    url="https://example.com/one",
                    registry_source="test",
                    verdict="trusted",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="scheme-https-2",
                    name="https-2",
                    url="https://example.com/two",
                    registry_source="test",
                    verdict="trusted",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="scheme-http-1",
                    name="http-1",
                    url="http://example.com/three",
                    registry_source="test",
                    verdict="conditional",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="scheme-empty-1",
                    name="empty-1",
                    url=None,
                    registry_source="test",
                    verdict="unknown",
                    risk_tier="unknown",
                ),
                McpServerRegistry(
                    server_id="scheme-malformed-1",
                    name="malformed-1",
                    url="example.com/four",
                    registry_source="test",
                    verdict="unknown",
                    risk_tier="unknown",
                ),
            ]
        )
        session.commit()

        def override_get_session():
            yield session

        that_app = FastAPI()
        that_app.include_router(router)
        that_app.dependency_overrides[get_session] = override_get_session

        response = TestClient(that_app).get("/api/registry/url-schemes")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 5

        buckets = {bucket["scheme"]: bucket for bucket in payload["buckets"]}
        assert buckets["https"]["count"] == 2
        assert buckets["https"]["pct"] == 40.0
        assert buckets["empty"]["count"] == 1
        assert buckets["malformed"]["count"] == 1
        assert buckets["http"]["count"] == 1

    engine.dispose()


if __name__ == "__main__":
    _self_test()
    print("PASS")