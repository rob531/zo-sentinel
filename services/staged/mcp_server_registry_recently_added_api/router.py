from datetime import datetime
from typing import Any, Mapping

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_recently_added_servers

router = APIRouter(prefix="/api", tags=["registry"])


class RecentlyAddedServer(BaseModel):
    server_id: str
    name: str
    registry_source: str | None = None
    verdict: str | None = None
    risk_tier: str | None = None
    trust_score: float | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class RecentlyAddedServersResponse(BaseModel):
    count: int
    servers: list[RecentlyAddedServer]


@router.get(
    "/registry/recently-added",
    response_model=RecentlyAddedServersResponse,
)
def recently_added_servers(
    limit: int = Query(default=10, gt=0),
    session: Session = Depends(get_session),
) -> RecentlyAddedServersResponse:
    result = get_recently_added_servers(session, limit=limit)

    if isinstance(result, Mapping) and "servers" in result:
        rows = list(result["servers"])
    else:
        rows = list(result)

    fields = (
        "server_id",
        "name",
        "registry_source",
        "verdict",
        "risk_tier",
        "trust_score",
        "first_seen",
        "last_seen",
    )
    servers: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            values = row
        elif hasattr(row, "_mapping"):
            values = row._mapping
        else:
            values = {field: getattr(row, field, None) for field in fields}
        servers.append({field: values.get(field) for field in fields})

    return RecentlyAddedServersResponse(count=len(servers), servers=servers)


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
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="oldest",
                    name="Oldest Server",
                    registry_source="registry",
                    trust_score=90.0,
                    verdict="trusted",
                    risk_tier="low",
                    first_seen=datetime(2024, 1, 1, 0, 0, 0),
                    last_seen=datetime(2024, 1, 3, 0, 0, 0),
                    last_assessed=datetime(2024, 1, 3, 0, 0, 0),
                    scan_count=3,
                ),
                McpServerRegistry(
                    server_id="middle",
                    name="Middle Server",
                    registry_source="registry",
                    trust_score=80.0,
                    verdict="conditional",
                    risk_tier="medium",
                    first_seen=datetime(2024, 1, 2, 0, 0, 0),
                    last_seen=datetime(2024, 1, 3, 0, 0, 0),
                    last_assessed=datetime(2024, 1, 3, 0, 0, 0),
                    scan_count=2,
                ),
                McpServerRegistry(
                    server_id="newest",
                    name="Newest Server",
                    registry_source="registry",
                    trust_score=70.0,
                    verdict="review",
                    risk_tier="high",
                    first_seen=datetime(2024, 1, 3, 0, 0, 0),
                    last_seen=datetime(2024, 1, 3, 0, 0, 0),
                    last_assessed=datetime(2024, 1, 3, 0, 0, 0),
                    scan_count=1,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/recently-added?limit=3")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    assert payload["servers"][0]["server_id"] == "newest"
    print("PASS")


if __name__ == "__main__":
    _self_test()