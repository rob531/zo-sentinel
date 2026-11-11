from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective
from .logic import get_facet_filter_profile

router = APIRouter(prefix="/api", tags=["perspectives"])


class FacetFilterKeyCount(BaseModel):
    key: str
    count: int


class FacetFilterProfileResponse(BaseModel):
    total: int
    with_filters: int
    without_filters: int
    avg_keys: float
    keys: list[FacetFilterKeyCount]
    axis_keyed: int
    non_axis_keyed: int


@router.get(
    "/perspectives/facet-filter-profile",
    response_model=FacetFilterProfileResponse,
)
def facet_filter_profile(
    session: Session = Depends(get_session),
) -> FacetFilterProfileResponse:
    return get_facet_filter_profile(session)


def _self_test() -> None:
    from uuid import uuid4

    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Perspective.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as session:
        session.add_all(
            [
                Perspective(
                    id=str(uuid4()),
                    org_id=str(uuid4()),
                    name="Axis filters",
                    description="",
                    facet_filters={"auth_strength": ["high"]},
                    created_by=str(uuid4()),
                ),
                Perspective(
                    id=str(uuid4()),
                    org_id=str(uuid4()),
                    name="Custom filters",
                    description="",
                    facet_filters={"custom_tag": ["custom"]},
                    created_by=str(uuid4()),
                ),
                Perspective(
                    id=str(uuid4()),
                    org_id=str(uuid4()),
                    name="No filters",
                    description="",
                    facet_filters=None,
                    created_by=str(uuid4()),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/perspectives/facet-filter-profile")
    assert response.status_code == 200

    payload = response.json()
    assert payload["total"] == 3
    assert payload["with_filters"] == 2
    assert payload["without_filters"] == 1
    assert payload["axis_keyed"] == 1
    assert payload["non_axis_keyed"] == 1
    assert any(
        item["key"] == "auth_strength" and item["count"] == 1
        for item in payload["keys"]
    )

    print("PASS")


if __name__ == "__main__":
    _self_test()