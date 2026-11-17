from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot
from services.staged.perspective_facet_diversity_api.router import router


def _column_value(column, ordinal):
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        return str(ordinal)
    if python_type is int:
        return ordinal
    if python_type is UUID:
        return UUID(int=ordinal)
    return f"{ordinal:032x}"


@contextmanager
def _seeded_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Perspective.__table__.create(bind=engine)
    PerspectiveSnapshot.__table__.create(bind=engine)

    perspective_id = _column_value(Perspective.__table__.c.id, 1)
    empty_perspective_id = _column_value(Perspective.__table__.c.id, 2)
    org_id = _column_value(Perspective.__table__.c.org_id, 101)
    created_by = _column_value(Perspective.__table__.c.created_by, 201)
    created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)

    with session_factory() as session:
        session.add_all(
            [
                Perspective(
                    id=perspective_id,
                    org_id=org_id,
                    name="Known Perspective",
                    description="Perspective with facet filters",
                    facet_filters={
                        "environment": "prod",
                        "region": "prod",
                        "criticality": "high",
                    },
                    created_by=created_by,
                    created_at=created_at,
                    updated_at=created_at,
                ),
                Perspective(
                    id=empty_perspective_id,
                    org_id=org_id,
                    name="Empty Filters Perspective",
                    description="Perspective without facet filters",
                    facet_filters={},
                    created_by=created_by,
                    created_at=created_at,
                    updated_at=created_at,
                ),
            ]
        )
        session.flush()

        session.add_all(
            [
                PerspectiveSnapshot(
                    id=1,
                    perspective_id=perspective_id,
                    taken_at=datetime(2024, 1, 2, 12, 0, tzinfo=timezone.utc),
                    membership={"members": ["alpha"]},
                ),
                PerspectiveSnapshot(
                    id=2,
                    perspective_id=perspective_id,
                    taken_at=datetime(2024, 1, 3, 12, 0, tzinfo=timezone.utc),
                    membership={"members": ["alpha", "beta"]},
                ),
                PerspectiveSnapshot(
                    id=3,
                    perspective_id=empty_perspective_id,
                    taken_at=datetime(2024, 1, 2, 13, 0, tzinfo=timezone.utc),
                    membership={"members": ["gamma"]},
                ),
            ]
        )
        session.commit()
        yield session_factory

    engine.dispose()


def _include_router(test_app):
    route_paths = [getattr(route, "path", "") for route in router.routes]
    if any(path == "/api" or path.startswith("/api/") for path in route_paths):
        test_app.include_router(router)
    else:
        test_app.include_router(router, prefix="/api")


def run_contract():
    with _seeded_session() as session_factory:
        test_app = FastAPI()
        _include_router(test_app)

        def override_get_session():
            with session_factory() as session:
                yield session

        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get("/api/perspectives/facet-diversity")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert "perspectives" in payload
            perspectives = {item["name"]: item for item in payload["perspectives"]}

            known = perspectives["Known Perspective"]
            assert known["facet_keys"] == 3
            assert known["facet_values"] == 2
            assert known["snapshots"] == 2
            assert known["latest_snapshot"] is not None
            assert isinstance(known["perspective_id"], str)

            empty = perspectives["Empty Filters Perspective"]
            assert empty["facet_keys"] == 0
            assert empty["facet_values"] == 0

    print("PASS")


if __name__ == "__main__":
    run_contract()