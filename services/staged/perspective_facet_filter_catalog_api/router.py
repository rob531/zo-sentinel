from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective

from .logic import get_facet_catalog


router = APIRouter(prefix="/api")


class FacetCatalogValue(BaseModel):
    value: str
    count: int


class FacetCatalogKey(BaseModel):
    key: str
    perspective_count: int
    values: list[FacetCatalogValue]


class FacetCatalogResponse(BaseModel):
    keys: list[FacetCatalogKey]


@router.get(
    "/perspectives/facet-catalog",
    response_model=FacetCatalogResponse,
)
def read_facet_catalog(
    session: Session = Depends(get_session),
) -> FacetCatalogResponse:
    return FacetCatalogResponse.model_validate(get_facet_catalog(session))


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    Perspective.__table__.create(bind=test_engine)

    with TestSession() as session:
        session.add_all(
            [
                Perspective(
                    id="11111111-1111-1111-1111-111111111111",
                    org_id="22222222-2222-2222-2222-222222222222",
                    name="Production",
                    description="Production perspectives",
                    facet_filters={
                        "environment": ["production", "staging"],
                        "region": ["us-east"],
                    },
                    created_by="33333333-3333-3333-3333-333333333333",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                ),
                Perspective(
                    id="44444444-4444-4444-4444-444444444444",
                    org_id="22222222-2222-2222-2222-222222222222",
                    name="Staging",
                    description="Staging perspectives",
                    facet_filters={
                        "environment": ["production"],
                        "region": ["us-west"],
                    },
                    created_by="33333333-3333-3333-3333-333333333333",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                ),
                Perspective(
                    id="55555555-5555-5555-5555-555555555555",
                    org_id="22222222-2222-2222-2222-222222222222",
                    name="Unfiltered",
                    description="Perspective without facet filters",
                    facet_filters={},
                    created_by="33333333-3333-3333-3333-333333333333",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/perspectives/facet-catalog")
        assert response.status_code == 200
        payload = response.json()
        keys = {item["key"]: item for item in payload["keys"]}
        assert keys["environment"]["perspective_count"] == 2
        environment_values = {
            item["value"]: item["count"]
            for item in keys["environment"]["values"]
        }
        assert environment_values == {"production": 2, "staging": 1}

    print("PASS")