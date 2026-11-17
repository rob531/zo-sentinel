from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

try:
    from .logic import get_axis_adapter_sha_mix
except ImportError:
    from .logic import get_adapter_sha_mix as get_axis_adapter_sha_mix


router = APIRouter(prefix="/api", tags=["axis"])


class AdapterShaMixEntry(BaseModel):
    adapter_sha256: str
    row_count: int
    distinct_servers: int
    distinct_axes: int
    first_scored_at: datetime | None = None
    last_scored_at: datetime | None = None


class AdapterShaMixResponse(BaseModel):
    total_adapters: int
    adapters: list[AdapterShaMixEntry]


@router.get(
    "/axis/adapter-sha-mix",
    response_model=AdapterShaMixResponse,
    summary="Summarize axis scores by adapter SHA-256",
)
def get_adapter_sha_mix(
    session: Session = Depends(get_session),
) -> AdapterShaMixResponse:
    result: Any = get_axis_adapter_sha_mix(session)

    if isinstance(result, Mapping):
        adapters = list(result.get("adapters", []))
        total_adapters = int(result.get("total_adapters", len(adapters)))
    else:
        adapters = list(result or [])
        total_adapters = len(adapters)

    return AdapterShaMixResponse(
        total_adapters=total_adapters,
        adapters=[AdapterShaMixEntry.model_validate(row) for row in adapters],
    )


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE {McpLlmAxisScore.__tablename__} (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR NOT NULL,
                    axis_name VARCHAR NOT NULL,
                    adapter_sha256 VARCHAR NOT NULL,
                    scored_at TIMESTAMP
                )
                """
            )
        )

    first_time = datetime(2025, 1, 1, 12, 0, 0)
    adapter_a = "a" * 64
    adapter_b = "b" * 64

    with TestSession() as session:
        session.execute(
            text(
                f"""
                INSERT INTO {McpLlmAxisScore.__tablename__}
                    (server_id, axis_name, adapter_sha256, scored_at)
                VALUES
                    (:server_id, :axis_name, :adapter_sha256, :scored_at)
                """
            ),
            [
                {
                    "server_id": "server-1",
                    "axis_name": "critical",
                    "adapter_sha256": adapter_a,
                    "scored_at": first_time,
                },
                {
                    "server_id": "server-2",
                    "axis_name": "critical",
                    "adapter_sha256": adapter_a,
                    "scored_at": first_time + timedelta(minutes=1),
                },
                {
                    "server_id": "server-3",
                    "axis_name": "policy",
                    "adapter_sha256": adapter_b,
                    "scored_at": first_time + timedelta(minutes=2),
                },
            ],
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get("/api/axis/adapter-sha-mix")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_adapters"] == 2, payload
        adapters = {item["adapter_sha256"]: item for item in payload["adapters"]}
        assert adapters[adapter_a]["row_count"] == 2, payload
        assert adapters[adapter_a]["distinct_servers"] == 2, payload
        assert adapters[adapter_b]["row_count"] == 1, payload

    print("PASS")