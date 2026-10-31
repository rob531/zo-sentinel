from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_ageing_cohorts

router = APIRouter(prefix="/api", tags=["registry"])


class AgeingCohortServer(BaseModel):
    server_id: str
    name: str | None = None
    last_seen: datetime | None = None
    risk_tier: str | None = None


class AgeingCohort(BaseModel):
    cohort: Literal["fresh", "recent", "stale", "dormant"]
    count: int = Field(ge=0)
    servers: list[AgeingCohortServer] = Field(default_factory=list)


class AgeingCohortsResponse(BaseModel):
    days: int
    total_servers: int = Field(ge=0)
    cohorts: list[AgeingCohort]
    cutoff: datetime


@router.get(
    "/registry/ageing-cohorts",
    response_model=AgeingCohortsResponse,
    summary="Registry server ageing cohorts",
)
def ageing_cohorts(
    days: int = Query(90, description="Maximum age in days for the stale cohort"),
    limit: int = Query(50, description="Maximum servers returned per cohort"),
    db: Session = Depends(get_session),
) -> AgeingCohortsResponse:
    return get_ageing_cohorts(db, days=days, limit=limit)


def _self_test() -> None:
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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    newest = datetime(2025, 1, 1, 12, 0, 0)
    rows = [
        ("fresh-server", "Fresh server", newest, "low"),
        ("recent-server", "Recent server", newest - timedelta(days=8), "medium"),
        ("stale-server", "Stale server", newest - timedelta(days=31), "high"),
        ("dormant-server", "Dormant server", newest - timedelta(days=91), "critical"),
    ]
    for server_id, name, last_seen, risk_tier in rows:
        testing_session.add(
            McpServerRegistry(
                server_id=server_id,
                name=name,
                first_seen=last_seen,
                last_seen=last_seen,
                last_scanned=last_seen,
                scan_count=1,
                risk_tier=risk_tier,
            )
        )
    testing_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/ageing-cohorts")
    assert response.status_code == 200, response.text
    payload = response.json()
    cohorts = {entry["cohort"]: entry for entry in payload["cohorts"]}
    assert set(cohorts) == {"fresh", "recent", "stale", "dormant"}
    assert cohorts["fresh"]["count"] == 1
    assert all("server_id" in server for entry in cohorts.values() for server in entry["servers"])
    print("PASS")


if __name__ == "__main__":
    _self_test()