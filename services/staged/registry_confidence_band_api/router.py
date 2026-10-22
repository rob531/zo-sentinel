from __future__ import annotations

from inspect import isawaitable
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_confidence_bands


class ConfidenceBand(BaseModel):
    band: str
    count: int = Field(ge=0)
    by_tier: dict[str, int] = Field(default_factory=dict)


class ConfidenceBandResponse(BaseModel):
    total: int = Field(ge=0)
    null_confidence: int = Field(ge=0)
    bands: list[ConfidenceBand]


router = APIRouter(prefix="/api")

_BAND_ORDER = (
    "0.0-0.2",
    "0.2-0.4",
    "0.4-0.6",
    "0.6-0.8",
    "0.8-1.0",
)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    if hasattr(value, "_mapping"):
        return dict(value._mapping)
    raise TypeError("Confidence-band logic returned an unsupported result")


def _normalize_result(result: Any) -> ConfidenceBandResponse:
    if isinstance(result, tuple):
        if len(result) == 2:
            bands, null_confidence = result
            total = None
        elif len(result) == 3:
            bands, null_confidence, total = result
        else:
            raise TypeError("Confidence-band logic returned an unsupported tuple")
    else:
        payload = _as_dict(result)
        bands = payload.get("bands", [])
        null_confidence = payload.get("null_confidence", 0)
        total = payload.get("total")

    normalized: dict[str, ConfidenceBand] = {}
    for item in bands:
        band = _as_dict(item)
        by_tier = band.get("by_tier") or {}
        normalized[str(band["band"])] = ConfidenceBand(
            band=str(band["band"]),
            count=int(band.get("count", 0)),
            by_tier={str(tier): int(count) for tier, count in by_tier.items()},
        )

    ordered_bands = [
        normalized.get(
            name,
            ConfidenceBand(band=name, count=0, by_tier={}),
        )
        for name in _BAND_ORDER
    ]

    null_count = int(null_confidence or 0)
    calculated_total = sum(band.count for band in ordered_bands) + null_count

    return ConfidenceBandResponse(
        total=calculated_total if total is None else int(total),
        null_confidence=null_count,
        bands=ordered_bands,
    )


@router.get(
    "/registry/confidence-bands",
    response_model=ConfidenceBandResponse,
    summary="Summarize registry confidence bands",
)
async def confidence_bands(
    session: Session = Depends(get_session),
) -> ConfidenceBandResponse:
    result = get_confidence_bands(session)
    if isawaitable(result):
        result = await result
    return _normalize_result(result)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    verdict VARCHAR,
                    risk_tier VARCHAR,
                    confidence FLOAT,
                    last_assessed TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, verdict, risk_tier, confidence, last_assessed)
                VALUES
                    ('server-1', 'Server 1', 'trusted', 'low', 0.1, CURRENT_TIMESTAMP),
                    ('server-2', 'Server 2', 'trusted', 'medium', 0.35, CURRENT_TIMESTAMP),
                    ('server-3', 'Server 3', 'review', 'high', 0.5, CURRENT_TIMESTAMP),
                    ('server-4', 'Server 4', 'trusted', 'low', 0.9, CURRENT_TIMESTAMP),
                    ('server-5', 'Server 5', 'review', 'medium', NULL, CURRENT_TIMESTAMP)
                """
            )
        )

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/confidence-bands")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 5, payload
        assert payload["null_confidence"] == 1, payload
        assert [band["count"] for band in payload["bands"]] == [1, 1, 1, 0, 1], payload

    print("PASS")


if __name__ == "__main__":
    _self_test()