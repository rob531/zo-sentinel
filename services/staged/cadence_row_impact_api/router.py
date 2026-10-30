from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

from .logic import get_cadence_row_impact

router = APIRouter()


class CadenceJobImpact(BaseModel):
    job: str
    runs: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    total_rows_affected: int = Field(ge=0)
    avg_rows_affected: float = Field(ge=0)
    last_started_at: datetime | None = None
    avg_duration_seconds: float | None = Field(default=None, ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)


class CadenceRowImpactTotals(BaseModel):
    jobs: int = Field(ge=0)
    runs: int = Field(ge=0)


class CadenceRowImpactResponse(BaseModel):
    jobs: list[CadenceJobImpact] = Field(default_factory=list)
    totals: CadenceRowImpactTotals


@router.get(
    "/api/cadence/row-impact",
    response_model=CadenceRowImpactResponse,
)
def cadence_row_impact(
    job: str | None = None,
    session: Session = Depends(get_session),
) -> CadenceRowImpactResponse:
    return cast(
        CadenceRowImpactResponse,
        get_cadence_row_impact(session, job),
    )


def _self_test() -> None:
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    CadenceJobRun.__table__.create(engine)
    with testing_session() as session:
        started = datetime(2025, 1, 1, 12, 0, 0)
        session.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="succeeded",
                    started_at=started,
                    finished_at=started + timedelta(seconds=10),
                    rows_affected=5,
                    detail={"kind": "refresh"},
                ),
                CadenceJobRun(
                    job="alpha",
                    status="succeeded",
                    started_at=started + timedelta(minutes=1),
                    finished_at=started + timedelta(minutes=1, seconds=20),
                    rows_affected=3,
                    detail={"kind": "refresh"},
                ),
                CadenceJobRun(
                    job="beta",
                    status="failed",
                    started_at=started + timedelta(minutes=2),
                    finished_at=started + timedelta(minutes=2, seconds=5),
                    rows_affected=0,
                    detail={"kind": "refresh"},
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session() -> Any:
        with testing_session() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/cadence/row-impact")
            assert response.status_code == 200
            payload = response.json()
            assert len(payload["jobs"]) == 2
            assert payload["totals"] == {"jobs": 2, "runs": 3}

            alpha = next(item for item in payload["jobs"] if item["job"] == "alpha")
            beta = next(item for item in payload["jobs"] if item["job"] == "beta")
            assert alpha["total_rows_affected"] == 8
            assert alpha["status_counts"]["succeeded"] == 2
            assert beta["status_counts"]["failed"] == 1
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()