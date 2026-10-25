from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective


router = APIRouter(prefix="/api")


class FacetCatalogueValue(BaseModel):
    value: str
    perspective_count: int


class FacetCatalogueEntry(BaseModel):
    key: str
    value_count: int
    values: List[FacetCatalogueValue]


class FacetCatalogueResponse(BaseModel):
    facets: List[FacetCatalogueEntry]
    total_perspectives: int


def _decode_facet_filters(raw: Any) -> Any:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None
    return raw


def _normalise_values(raw: Any) -> List[str]:
    if raw is None:
        return []

    if isinstance(raw, dict):
        if "value" in raw:
            return _normalise_values(raw["value"])
        if "values" in raw:
            return _normalise_values(raw["values"])
        values: List[str] = []
        for nested in raw.values():
            values.extend(_normalise_values(nested))
        return values

    if isinstance(raw, (list, tuple, set)):
        values = []
        for nested in raw:
            values.extend(_normalise_values(nested))
        return values

    if isinstance(raw, bool):
        return [str(raw).lower()]
    return [str(raw)]


def _iter_facet_entries(raw: Any) -> Iterable[Tuple[str, List[str]]]:
    if isinstance(raw, dict):
        if "key" in raw and "value" in raw:
            yield str(raw["key"]), _normalise_values(raw["value"])
            return

        for key, values in raw.items():
            yield str(key), _normalise_values(values)
        return

    if isinstance(raw, (list, tuple)):
        for entry in raw:
            yield from _iter_facet_entries(entry)


def build_facet_catalogue(
    session: Session,
    perspective_id: Optional[str] = None,
) -> FacetCatalogueResponse:
    statement = select(Perspective.id, Perspective.name, Perspective.facet_filters)
    if perspective_id is not None:
        statement = statement.where(Perspective.id == perspective_id)

    rows = session.execute(statement).all()

    values_by_key: Dict[str, Dict[str, Set[str]]] = {}
    perspectives_by_key: Dict[str, Set[str]] = {}

    for row in rows:
        perspective_identifier = str(row.id)
        decoded = _decode_facet_filters(row.facet_filters)
        for key, values in _iter_facet_entries(decoded):
            key_values = values_by_key.setdefault(key, {})
            perspectives_by_key.setdefault(key, set())
            for value in values:
                key_values.setdefault(value, set()).add(perspective_identifier)

    facets: List[FacetCatalogueEntry] = []
    for key in sorted(values_by_key):
        key_values = values_by_key[key]
        facet_values = [
            FacetCatalogueValue(
                value=value,
                perspective_count=len(key_values[value]),
            )
            for value in sorted(key_values)
        ]
        facets.append(
            FacetCatalogueEntry(
                key=key,
                value_count=len(facet_values),
                values=facet_values,
            )
        )

    return FacetCatalogueResponse(
        facets=facets,
        total_perspectives=len(rows),
    )


@router.get(
    "/perspectives/facet-catalogue",
    response_model=FacetCatalogueResponse,
)
def get_facet_catalogue(
    session: Session = Depends(get_session),
    perspective_id: Optional[str] = Query(default=None),
) -> FacetCatalogueResponse:
    return build_facet_catalogue(session=session, perspective_id=perspective_id)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Perspective.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    seed_rows = [
        {
            "id": "perspective-1",
            "org_id": "org-1",
            "name": "First",
            "description": "First perspective",
            "facet_filters": {"cloud": ["aws", "azure"], "severity": ["high"]},
            "created_by": "user-1",
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": "perspective-2",
            "org_id": "org-1",
            "name": "Second",
            "description": "Second perspective",
            "facet_filters": {"cloud": ["aws"], "severity": ["medium", "high"]},
            "created_by": "user-1",
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": "perspective-3",
            "org_id": "org-1",
            "name": "Third",
            "description": "Third perspective",
            "facet_filters": {"cloud": ["gcp"], "region": ["us"]},
            "created_by": "user-1",
            "created_at": now,
            "updated_at": now,
        },
    ]

    with SessionLocal() as session:
        session.execute(Perspective.__table__.insert(), seed_rows)
        session.commit()

    def override_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_session

    with TestClient(that_app) as client:
        response = client.get("/api/perspectives/facet-catalogue")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_perspectives"] == 3
        assert {facet["key"] for facet in payload["facets"]} == {
            "cloud",
            "severity",
            "region",
        }
        cloud = next(
            facet for facet in payload["facets"] if facet["key"] == "cloud"
        )
        assert cloud["value_count"] == 3
        assert next(
            value for value in cloud["values"] if value["value"] == "aws"
        )["perspective_count"] == 2

    print("PASS")