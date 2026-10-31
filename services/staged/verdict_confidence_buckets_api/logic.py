from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class ConfidenceBand(BaseModel):
    band: str
    count: int = 0
    verdicts: Dict[str, int] = Field(default_factory=dict)
    sources: Dict[str, int] = Field(default_factory=dict)


class ConfidenceBucketsResponse(BaseModel):
    total: int
    bands: List[ConfidenceBand]
    generated_at: datetime


router = APIRouter(prefix="/api/verdict", tags=["verdict"])


def _bucket_rows(session: Session, registry_source: Optional[str]):
    band = case(
        (McpServerRegistry.confidence >= 0.9, "high"),
        (McpServerRegistry.confidence >= 0.7, "medium"),
        (McpServerRegistry.confidence >= 0.5, "low"),
        else_="minimal",
    )
    verdict = func.coalesce(
        func.nullif(McpServerRegistry.verdict, ""),
        "unknown",
    )
    source = func.coalesce(
        func.nullif(McpServerRegistry.registry_source, ""),
        "unknown",
    )

    statement = (
        select(
            band.label("band"),
            verdict.label("verdict"),
            source.label("registry_source"),
            func.count().label("row_count"),
        )
        .group_by(band, verdict, source)
    )
    if registry_source is not None:
        statement = statement.where(
            func.coalesce(
                func.nullif(McpServerRegistry.registry_source, ""),
                "unknown",
            )
            == registry_source
        )

    return session.execute(statement).all()


def build_confidence_buckets(
    session: Session,
    registry_source: Optional[str] = None,
) -> ConfidenceBucketsResponse:
    bands = {
        name: {"band": name, "count": 0, "verdicts": {}, "sources": {}}
        for name in ("high", "medium", "low", "minimal")
    }

    total = 0
    for row in _bucket_rows(session, registry_source):
        bucket = bands[row.band]
        count = int(row.row_count)
        bucket["count"] += count
        total += count
        bucket["verdicts"][row.verdict] = (
            bucket["verdicts"].get(row.verdict, 0) + count
        )
        bucket["sources"][row.registry_source] = (
            bucket["sources"].get(row.registry_source, 0) + count
        )

    return ConfidenceBucketsResponse(
        total=total,
        bands=[ConfidenceBand(**bands[name]) for name in ("high", "medium", "low", "minimal")],
        generated_at=datetime.now(timezone.utc),
    )


@router.get(
    "/confidence-buckets",
    response_model=ConfidenceBucketsResponse,
    summary="Get confidence buckets for registry verdicts",
)
def confidence_buckets(
    registry_source: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> ConfidenceBucketsResponse:
    return build_confidence_buckets(session=session, registry_source=registry_source)


def create_app() -> FastAPI:
    application = FastAPI(title="verdict_confidence_buckets_api")
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as test_session:
        test_session.execute(
            McpServerRegistry.__table__.insert(),
            [
                {
                    "server_id": "confidence-test-1",
                    "name": "test-1",
                    "registry_source": "source-a",
                    "verdict": "trusted",
                    "confidence": 0.95,
                },
                {
                    "server_id": "confidence-test-2",
                    "name": "test-2",
                    "registry_source": "source-a",
                    "verdict": "trusted",
                    "confidence": 0.75,
                },
                {
                    "server_id": "confidence-test-3",
                    "name": "test-3",
                    "registry_source": "source-b",
                    "verdict": "blocked",
                    "confidence": 0.6,
                },
                {
                    "server_id": "confidence-test-4",
                    "name": "test-4",
                    "registry_source": "source-b",
                    "verdict": "blocked",
                    "confidence": 0.2,
                },
            ],
        )
        test_session.commit()

        that_app = FastAPI()
        that_app.include_router(router)

        def override_get_session():
            yield test_session

        that_app.dependency_overrides[get_session] = override_get_session

        result = build_confidence_buckets(test_session)
        assert result.total == 4
        assert sum(item.count for item in result.bands) == 4
        assert sum(
            count
            for item in result.bands
            for count in item.verdicts.values()
        ) == 4
        assert result.bands[0].verdicts["trusted"] == 1
        assert result.bands[1].verdicts["trusted"] == 1
        assert result.bands[2].verdicts["blocked"] == 1
        assert result.bands[3].verdicts["blocked"] == 1

    print("PASS")