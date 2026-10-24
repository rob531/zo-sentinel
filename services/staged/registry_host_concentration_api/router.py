from __future__ import annotations

from inspect import isawaitable
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

if __package__:
    from .logic import get_host_concentration as _get_host_concentration
else:
    from logic import get_host_concentration as _get_host_concentration


class HostConcentrationItem(BaseModel):
    host: str
    server_count: int
    verdicts: dict[str, int]
    distinct_verdicts: int
    risk_tiers: list[str]

    class Config:
        orm_mode = True


class HostConcentrationResponse(BaseModel):
    total_servers: int
    distinct_hosts: int
    hosts: list[HostConcentrationItem]

    class Config:
        orm_mode = True


router = APIRouter(prefix="/api")


@router.get(
    "/registry/host-concentration",
    response_model=HostConcentrationResponse,
)
async def get_host_concentration(
    session: Session = Depends(get_session),
) -> Any:
    result = _get_host_concentration(session)
    if isawaitable(result):
        result = await result

    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "dict"):
        return result.dict()
    return result


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

    testing_session.add_all(
        [
            McpServerRegistry(
                server_id="host-concentration-1",
                name="shared-one",
                url="https://shared.example/path-one",
                registry_source="self-test",
                verdict="trusted",
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="host-concentration-2",
                name="shared-two",
                url="https://shared.example/path-two",
                registry_source="self-test",
                verdict="suspicious",
                risk_tier="medium",
            ),
            McpServerRegistry(
                server_id="host-concentration-3",
                name="single-one",
                url="https://single-one.example/path",
                registry_source="self-test",
                verdict="trusted",
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="host-concentration-4",
                name="single-two",
                url="https://single-two.example/path",
                registry_source="self-test",
                verdict="unknown",
                risk_tier="high",
            ),
        ]
    )
    testing_session.commit()

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session() -> Session:
        return testing_session

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/registry/host-concentration")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_servers"] == 4
        assert payload["distinct_hosts"] == 3
        shared = next(host for host in payload["hosts"] if host["host"] == "shared.example")
        assert shared["server_count"] == 2
        assert shared["distinct_verdicts"] == 2

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()