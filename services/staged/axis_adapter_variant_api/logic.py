from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def get_adapter_variant_rollup(
    session: Session,
    days: int = 30,
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = session.execute(
        select(
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.scored_at,
        ).where(McpLlmAxisScore.scored_at >= cutoff)
    ).all()

    buckets: dict[tuple[str | None, str | None], dict[str, Any]] = defaultdict(
        lambda: {
            "rows": 0,
            "servers": set(),
            "first_scored_at": None,
            "last_scored_at": None,
        }
    )
    adapters: set[str | None] = set()
    models: set[str | None] = set()

    for adapter_sha256, model_version, server_id, scored_at in rows:
        key = (adapter_sha256, model_version)
        bucket = buckets[key]
        bucket["rows"] += 1
        bucket["servers"].add(server_id)

        if adapter_sha256 is not None:
            adapters.add(adapter_sha256)
        if model_version is not None:
            models.add(model_version)

        if scored_at is not None:
            if bucket["first_scored_at"] is None or scored_at < bucket["first_scored_at"]:
                bucket["first_scored_at"] = scored_at
            if bucket["last_scored_at"] is None or scored_at > bucket["last_scored_at"]:
                bucket["last_scored_at"] = scored_at

    adapter_rows = [
        {
            "adapter_sha256": adapter_sha256,
            "model_version": model_version,
            "rows": bucket["rows"],
            "distinct_servers": len(bucket["servers"]),
            "first_scored_at": _isoformat(bucket["first_scored_at"]),
            "last_scored_at": _isoformat(bucket["last_scored_at"]),
        }
        for (adapter_sha256, model_version), bucket in sorted(
            buckets.items(),
            key=lambda item: (item[0][0] or "", item[0][1] or ""),
        )
    ]

    return {
        "adapters": adapter_rows,
        "distinct_adapters": len(adapters),
        "distinct_models": len(models),
        "total_rows": len(rows),
    }


get_adapter_variant_summary = get_adapter_variant_rollup
get_adapter_variant_coverage = get_adapter_variant_rollup
get_axis_adapter_variant = get_adapter_variant_rollup
axis_adapter_variant_rollup = get_adapter_variant_rollup


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    seeded_rows = [
        McpLlmAxisScore(
            id=1,
            server_id="server-1",
            axis_name="coverage",
            model_version="model-a",
            adapter_sha256="adapter-a",
            scored_at=now - timedelta(minutes=3),
        ),
        McpLlmAxisScore(
            id=2,
            server_id="server-2",
            axis_name="coverage",
            model_version="model-a",
            adapter_sha256="adapter-a",
            scored_at=now - timedelta(minutes=2),
        ),
        McpLlmAxisScore(
            id=3,
            server_id="server-3",
            axis_name="coverage",
            model_version="model-b",
            adapter_sha256="adapter-b",
            scored_at=now - timedelta(minutes=1),
        ),
    ]

    with SessionLocal() as session:
        session.add_all(seeded_rows)
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/axes/adapter-variant")
    def adapter_variant_endpoint(
        days: int = 30,
        session: Session = Depends(get_session),
    ):
        return get_adapter_variant_rollup(session, days)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axes/adapter-variant", params={"days": 30})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["distinct_adapters"] == 2, payload
    assert payload["distinct_models"] == 2, payload
    assert payload["total_rows"] == len(seeded_rows), payload
    assert any(bucket["distinct_servers"] == 2 for bucket in payload["adapters"]), payload

    print("PASS")


if __name__ == "__main__":
    _self_test()