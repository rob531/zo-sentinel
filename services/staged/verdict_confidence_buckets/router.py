"""HTTP surface for verdict confidence bucket reporting."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_confidence_buckets

router = APIRouter(prefix="/api/verdict", tags=["verdict"])

_BAND_ORDER = ("LOW", "MEDIUM", "HIGH", "VERY_HIGH")
_DEFAULT_BOUNDARIES = {
    "LOW": "< 0.30",
    "MEDIUM": "0.30 - 0.60",
    "HIGH": "0.60 - 0.85",
    "VERY_HIGH": ">= 0.85",
}


class ConfidenceBand(BaseModel):
    band: str
    count: int
    verdicts: dict[str, int]


class ConfidenceBucketsResponse(BaseModel):
    bands: list[ConfidenceBand]
    total_servers: int
    band_boundaries: dict[str, str]


def _response_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, BaseModel):
        return result.model_dump() if hasattr(result, "model_dump") else result.dict()
    if isinstance(result, dict):
        return result
    if isinstance(result, (list, tuple)):
        return {"bands": list(result)}
    raise TypeError("confidence bucket logic returned an unsupported value")


def _normalise_bands(raw_bands: Any) -> list[ConfidenceBand]:
    by_name = {
        item["band"]: item
        for item in raw_bands
        if isinstance(item, dict) and item.get("band") in _BAND_ORDER
    }
    bands: list[ConfidenceBand] = []
    for name in _BAND_ORDER:
        item = by_name.get(name, {})
        verdicts = {
            str(verdict): int(count)
            for verdict, count in (item.get("verdicts") or {}).items()
        }
        count = int(item.get("count", sum(verdicts.values())))
        bands.append(ConfidenceBand(band=name, count=count, verdicts=verdicts))
    return bands


@router.get("/confidence-buckets", response_model=ConfidenceBucketsResponse)
def confidence_buckets(
    db: Session = Depends(get_session),
) -> ConfidenceBucketsResponse:
    payload = _response_payload(get_confidence_buckets(db))
    bands = _normalise_bands(payload.get("bands", []))
    total_servers = int(
        payload.get("total_servers", sum(band.count for band in bands))
    )
    boundaries = payload.get("band_boundaries") or _DEFAULT_BOUNDARIES
    return ConfidenceBucketsResponse(
        bands=bands,
        total_servers=total_servers,
        band_boundaries={str(key): str(value) for key, value in boundaries.items()},
    )


def _self_test() -> None:
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as OrmSession
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)

    def override_get_session():
        with OrmSession(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with OrmSession(engine) as session:
        session.add_all(
            [
                McpServerRegistry(server_id="self-test-low", confidence=0.2, verdict="trusted"),
                McpServerRegistry(server_id="self-test-medium", confidence=0.5, verdict="review"),
                McpServerRegistry(server_id="self-test-high", confidence=0.9, verdict="blocked"),
            ]
        )
        session.commit()

    from fastapi.testclient import TestClient

    response = TestClient(app).get("/api/verdict/confidence-buckets")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["bands"]) == 4, body
    medium = next(band for band in body["bands"] if band["band"] == "MEDIUM")
    assert medium["count"] == 1, body
    assert body["total_servers"] == 3, body
    print("PASS")


if __name__ == "__main__":
    _self_test()