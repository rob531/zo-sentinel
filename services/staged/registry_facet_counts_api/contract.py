from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


CANONICAL_FACET_ORDER = [
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
]


class FacetBucket(BaseModel):
    key: Optional[str]
    count: int


class RegistryFacetCountsResponse(BaseModel):
    generated_at: str
    counts: Dict[str, List[FacetBucket]]
    total_servers: int


router = APIRouter(prefix="/api")


def _facet_buckets(
    session: Session,
    column,
    filters,
    canonical: Optional[List[str]] = None,
) -> List[FacetBucket]:
    statement = (
        select(column, func.count().label("bucket_count"))
        .where(*filters)
        .group_by(column)
    )
    rows = session.execute(statement).all()

    counts = {key: int(count) for key, count in rows}

    if canonical is None:
        ordered_keys = sorted(
            counts,
            key=lambda value: (value is None, value if value is not None else ""),
        )
    else:
        canonical_index = {value: index for index, value in enumerate(canonical)}
        ordered_keys = sorted(
            counts,
            key=lambda value: (
                value is None,
                canonical_index.get(value, len(canonical) + 1)
                if value not in canonical_index
                else canonical_index[value],
                value if value is not None else "",
            ),
        )

    return [FacetBucket(key=key, count=counts[key]) for key in ordered_keys]


@router.get(
    "/registry/facets",
    response_model=RegistryFacetCountsResponse,
)
def get_registry_facets(
    group_by: Optional[
        Literal["registry_source", "risk_tier", "verdict"]
    ] = Query(default=None),
    registry_source: Optional[str] = Query(default=None),
    risk_tier: Optional[str] = Query(default=None),
    verdict: Optional[str] = Query(default=None),
    include_unscored: bool = Query(default=True),
    session: Session = Depends(get_session),
) -> RegistryFacetCountsResponse:
    filters = []

    if registry_source is not None:
        filters.append(McpServerRegistry.registry_source == registry_source)
    if risk_tier is not None:
        filters.append(McpServerRegistry.risk_tier == risk_tier)
    if verdict is not None:
        filters.append(McpServerRegistry.verdict == verdict)

    if not include_unscored:
        filters.append(
            or_(
                McpServerRegistry.trust_score.is_(None),
                McpServerRegistry.risk_tier.is_(None),
                McpServerRegistry.verdict.is_(None),
            )
        )

    total_servers = int(
        session.execute(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(*filters)
        ).scalar_one()
    )

    counts: Dict[str, List[FacetBucket]] = {}
    dimensions = [group_by] if group_by is not None else [
        "registry_source",
        "risk_tier",
        "verdict",
    ]

    for dimension in dimensions:
        if dimension == "registry_source":
            counts[dimension] = _facet_buckets(
                session,
                McpServerRegistry.registry_source,
                filters,
            )
        elif dimension == "risk_tier":
            counts[dimension] = _facet_buckets(
                session,
                McpServerRegistry.risk_tier,
                filters,
                CANONICAL_FACET_ORDER,
            )
        else:
            counts[dimension] = _facet_buckets(
                session,
                McpServerRegistry.verdict,
                filters,
                CANONICAL_FACET_ORDER,
            )

    return RegistryFacetCountsResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        counts=counts,
        total_servers=total_servers,
    )


app = FastAPI(title="MCP Registry Facet Counts API")
app.include_router(router)


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session_factory = sessionmaker(bind=engine)

    rows = [
        McpServerRegistry(
            server_id="contract-1",
            name="Official One",
            registry_source="official",
            risk_tier="TRUSTED_GENERAL",
            verdict="TRUSTED_GENERAL",
            trust_score=0.95,
            last_assessed=datetime.now(timezone.utc),
        ),
        McpServerRegistry(
            server_id="contract-2",
            name="Official Two",
            registry_source="official",
            risk_tier="TRUSTED_RESEARCH",
            verdict="TRUSTED_RESEARCH",
            trust_score=0.90,
            last_assessed=datetime.now(timezone.utc),
        ),
        McpServerRegistry(
            server_id="contract-3",
            name="Community One",
            registry_source="community",
            risk_tier="HIGH_RISK_ISOLATED",
            verdict="KNOWN_THREAT",
            trust_score=0.20,
            last_assessed=datetime.now(timezone.utc),
        ),
        McpServerRegistry(
            server_id="contract-4",
            name="Community Two",
            registry_source="community",
            risk_tier=None,
            verdict="INSUFFICIENT",
            trust_score=0.40,
            last_assessed=datetime.now(timezone.utc),
        ),
        McpServerRegistry(
            server_id="contract-5",
            name="Community Three",
            registry_source="community",
            risk_tier="CAUTION_LIMITED",
            verdict=None,
            trust_score=0.60,
            last_assessed=datetime.now(timezone.utc),
        ),
    ]

    with testing_session_factory() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with testing_session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)
    response = client.get("/api/registry/facets")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["total_servers"] == 5
    registry_buckets = {
        bucket["key"]: bucket["count"]
        for bucket in payload["counts"]["registry_source"]
    }
    assert registry_buckets["official"] == 2

    risk_buckets = payload["counts"]["risk_tier"]
    null_risk = [bucket for bucket in risk_buckets if bucket["key"] is None]
    assert len(null_risk) == 1
    assert null_risk[0]["count"] == 1
    assert risk_buckets[-1]["key"] is None

    canonical_positions = [
        CANONICAL_FACET_ORDER.index(bucket["key"])
        for bucket in risk_buckets
        if bucket["key"] is not None
    ]
    assert canonical_positions == sorted(canonical_positions)

    filtered = client.get(
        "/api/registry/facets",
        params={"registry_source": "official"},
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total_servers"] == 2

    print("PASS")


if __name__ == "__main__":
    _run_self_test()