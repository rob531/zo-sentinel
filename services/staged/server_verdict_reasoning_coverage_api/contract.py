from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class RegistrySourceCoverage(BaseModel):
    registry_source: str
    total: int
    with_reasoning: int
    coverage_pct: float


class ReasoningCoverageResponse(BaseModel):
    total: int
    with_reasoning: int
    without_reasoning: int
    coverage_pct: float
    by_registry_source: List[RegistrySourceCoverage]


router = APIRouter(prefix="/api")


def _coverage_pct(with_reasoning: int, total: int) -> float:
    if total == 0:
        return 0.0
    return float(round((with_reasoning / total) * 100, 2))


def get_reasoning_coverage(
    registry_source: Optional[str],
    session: Session,
) -> ReasoningCoverageResponse:
    reasoning_present = case(
        (
            McpServerRegistry.verdict_reasoning.is_not(None)
            & (func.length(func.trim(McpServerRegistry.verdict_reasoning)) > 0),
            1,
        ),
        else_=0,
    )

    filters = []
    if registry_source is not None:
        filters.append(McpServerRegistry.registry_source == registry_source)

    totals_query = select(
        func.count().label("total"),
        func.sum(reasoning_present).label("with_reasoning"),
    )
    if filters:
        totals_query = totals_query.where(*filters)

    totals_row = session.execute(totals_query).one()
    total = int(totals_row.total or 0)
    with_reasoning = int(totals_row.with_reasoning or 0)

    grouped_query = (
        select(
            McpServerRegistry.registry_source.label("registry_source"),
            func.count().label("total"),
            func.sum(reasoning_present).label("with_reasoning"),
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
    )
    if filters:
        grouped_query = grouped_query.where(*filters)

    by_registry_source = []
    for row in session.execute(grouped_query).all():
        source_total = int(row.total or 0)
        source_with_reasoning = int(row.with_reasoning or 0)
        by_registry_source.append(
            RegistrySourceCoverage(
                registry_source=row.registry_source or "",
                total=source_total,
                with_reasoning=source_with_reasoning,
                coverage_pct=_coverage_pct(
                    source_with_reasoning,
                    source_total,
                ),
            )
        )

    return ReasoningCoverageResponse(
        total=total,
        with_reasoning=with_reasoning,
        without_reasoning=total - with_reasoning,
        coverage_pct=_coverage_pct(with_reasoning, total),
        by_registry_source=by_registry_source,
    )


@router.get(
    "/verdict/reasoning-coverage",
    response_model=ReasoningCoverageResponse,
)
def verdict_reasoning_coverage(
    registry_source: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> ReasoningCoverageResponse:
    return get_reasoning_coverage(registry_source, session)


def _run_self_test() -> None:
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
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    assessed_at = datetime.now(timezone.utc)
    rows = [
        {
            "server_id": "contract-server-1",
            "verdict": "pass",
            "verdict_reasoning": "Registry assessment passed all configured checks.",
            "registry_source": "source-a",
            "confidence": 0.95,
            "last_assessed": assessed_at,
        },
        {
            "server_id": "contract-server-2",
            "verdict": "warn",
            "verdict_reasoning": "Registry assessment identified a low-risk discrepancy.",
            "registry_source": "source-b",
            "confidence": 0.80,
            "last_assessed": assessed_at,
        },
        {
            "server_id": "contract-server-3",
            "verdict": "unknown",
            "verdict_reasoning": None,
            "registry_source": "source-a",
            "confidence": 0.40,
            "last_assessed": assessed_at,
        },
        {
            "server_id": "contract-server-4",
            "verdict": "unknown",
            "verdict_reasoning": "",
            "registry_source": "source-b",
            "confidence": 0.40,
            "last_assessed": assessed_at,
        },
    ]

    with session_factory() as session:
        session.execute(McpServerRegistry.__table__.insert(), rows)
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/verdict/reasoning-coverage")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["total"] == 4, payload
            assert payload["with_reasoning"] == 2, payload
            assert payload["without_reasoning"] == 2, payload
            assert payload["coverage_pct"] == 50.0, payload
            assert len(payload["by_registry_source"]) == 2, payload
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()
    raise SystemExit(0)