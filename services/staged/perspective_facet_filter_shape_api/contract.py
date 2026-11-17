from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, Generator, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Perspective


class PerspectiveFilterShapeItem(BaseModel):
    perspective_id: str
    name: Optional[str] = None
    org_id: Optional[str] = None
    filter_keys: List[str]
    filter_count: int


class PerspectiveFilterShape(BaseModel):
    perspectives: List[PerspectiveFilterShapeItem]
    filter_key_frequency: Dict[str, int]
    total_perspectives: int


def get_perspective_filter_shape(db: Session) -> PerspectiveFilterShape:
    rows = db.execute(
        text("SELECT id, org_id, name, facet_filters FROM perspectives")
    ).mappings().all()

    perspectives: List[PerspectiveFilterShapeItem] = []
    frequency: Counter = Counter()

    for row in rows:
        raw_filters = row["facet_filters"]
        if raw_filters is None:
            continue

        try:
            parsed_filters = json.loads(raw_filters)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

        if not isinstance(parsed_filters, dict):
            continue

        filter_keys = [str(key) for key in parsed_filters.keys()]
        frequency.update(filter_keys)
        perspectives.append(
            PerspectiveFilterShapeItem(
                perspective_id=str(row["id"]),
                name=row["name"],
                org_id=None if row["org_id"] is None else str(row["org_id"]),
                filter_keys=filter_keys,
                filter_count=len(filter_keys),
            )
        )

    return PerspectiveFilterShape(
        perspectives=perspectives,
        filter_key_frequency=dict(frequency),
        total_perspectives=len(rows),
    )


router = APIRouter(prefix="/api")


@router.get(
    "/perspectives/facet-filter-shape",
    response_model=PerspectiveFilterShape,
)
def facet_filter_shape(db: Session = Depends(get_session)) -> PerspectiveFilterShape:
    return get_perspective_filter_shape(db)


app = FastAPI()
app.include_router(router)


def _run_self_test() -> None:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_engine,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE perspectives (
                    id VARCHAR PRIMARY KEY,
                    org_id VARCHAR,
                    name VARCHAR,
                    description TEXT,
                    facet_filters TEXT,
                    created_by VARCHAR,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO perspectives
                    (id, org_id, name, description, facet_filters, created_by)
                VALUES
                    (:id, :org_id, :name, :description, :facet_filters, :created_by)
                """
            ),
            [
                {
                    "id": "perspective-1",
                    "org_id": "org-1",
                    "name": "Tier One",
                    "description": None,
                    "facet_filters": json.dumps(
                        {"tier": ["HIGH_RISK_ISOLATED"], "source": ["npm"]}
                    ),
                    "created_by": "user-1",
                },
                {
                    "id": "perspective-2",
                    "org_id": "org-1",
                    "name": "Tier Two",
                    "description": None,
                    "facet_filters": json.dumps({"tier": []}),
                    "created_by": "user-1",
                },
                {
                    "id": "perspective-3",
                    "org_id": "org-1",
                    "name": "Unfiltered",
                    "description": None,
                    "facet_filters": None,
                    "created_by": "user-1",
                },
            ],
        )

    def override_get_session() -> Generator[Session, None, None]:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(app) as client:
            response = client.get("/api/perspectives/facet-filter-shape")
            assert response.status_code == 200
            payload = response.json()
            assert payload["filter_key_frequency"]["tier"] == 2
            assert payload["total_perspectives"] == 3
    finally:
        app.dependency_overrides.clear()
        test_engine.dispose()


if __name__ == "__main__":
    _run_self_test()
    print("PASS")