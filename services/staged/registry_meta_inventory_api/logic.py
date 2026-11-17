from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any, List

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class RegistrySourceCount(BaseModel):
    registry_source: str
    count: int


class MetaKeyCount(BaseModel):
    key: str
    count: int


class MetaInventoryResponse(BaseModel):
    total_servers: int
    sources: List[RegistrySourceCount]
    meta_key_histogram: List[MetaKeyCount]
    meta_missing_count: int
    meta_coverage_share: float
    generated_at: str


def _decode_meta(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


def get_meta_inventory(session: Session) -> MetaInventoryResponse:
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.registry_source,
            McpServerRegistry.meta,
        )
    ).all()

    source_counts: Counter[str] = Counter()
    meta_key_counts: Counter[str] = Counter()
    meta_missing_count = 0

    for row in rows:
        source_counts[str(row.registry_source)] += 1
        if row.meta is None:
            meta_missing_count += 1
            continue

        meta = _decode_meta(row.meta)
        if isinstance(meta, dict):
            meta_key_counts.update(str(key) for key in meta)
        elif isinstance(meta, list):
            for item in meta:
                if isinstance(item, dict):
                    meta_key_counts.update(str(key) for key in item)

    total_servers = len(rows)
    meta_coverage_share = (
        (total_servers - meta_missing_count) / total_servers if total_servers else 0.0
    )

    return MetaInventoryResponse(
        total_servers=total_servers,
        sources=[
            RegistrySourceCount(registry_source=source, count=count)
            for source, count in sorted(source_counts.items())
        ],
        meta_key_histogram=[
            MetaKeyCount(key=key, count=count)
            for key, count in sorted(meta_key_counts.items())
        ],
        meta_missing_count=meta_missing_count,
        meta_coverage_share=meta_coverage_share,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from services.staged.registry_meta_inventory_api.router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    testing_session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    try:
        with testing_session_local() as session:
            session.add_all(
                [
                    McpServerRegistry(
                        server_id="server-1",
                        name="Server One",
                        registry_source="official",
                        meta=json.dumps({"tags": ["python"]}),
                    ),
                    McpServerRegistry(
                        server_id="server-2",
                        name="Server Two",
                        registry_source="community",
                        meta=json.dumps({"tags": ["security"]}),
                    ),
                    McpServerRegistry(
                        server_id="server-3",
                        name="Server Three",
                        registry_source="official",
                        meta=None,
                    ),
                ]
            )
            session.commit()

        def override_get_session():
            with testing_session_local() as session:
                yield session

        that_app = FastAPI()
        that_app.include_router(router, prefix="/api")
        that_app.dependency_overrides[get_session] = override_get_session

        with TestClient(that_app) as client:
            response = client.get("/api/registry/meta-inventory")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_servers"] == 3
            assert payload["meta_missing_count"] == 1
            assert any(
                item["key"] == "tags" and item["count"] == 2
                for item in payload["meta_key_histogram"]
            )
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()