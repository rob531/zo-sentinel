from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_scan_count_histogram
except ImportError:
    from logic import get_scan_count_histogram


router = APIRouter(prefix="/api", tags=["scan-count-histogram"])


class ScanCountBand(BaseModel):
    band: str
    count: int
    unscanned: int
    by_tier: Dict[str, int]


class ScanCountHistogramResponse(BaseModel):
    bands: List[ScanCountBand]
    total_servers: int
    unscanned_total: int


_BAND_ORDER = ("0-1", "2-3", "4-9", "10-49", "50+")


def _as_dict(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, BaseModel):
        if hasattr(payload, "model_dump"):
            return payload.model_dump()
        return payload.dict()
    return dict(payload)


def _normalize_response(payload: Any) -> ScanCountHistogramResponse:
    data = _as_dict(payload)
    raw_bands = data.get("bands") or []

    bands: Dict[str, Dict[str, Any]] = {}
    unscanned_total = int(data.get("unscanned_total") or 0)

    for raw_band in raw_bands:
        band_data = _as_dict(raw_band)
        name = str(band_data.get("band") or "")
        count = int(band_data.get("count") or 0)

        if name.lower() == "unscanned":
            unscanned_total = max(
                unscanned_total,
                count,
                int(band_data.get("unscanned") or 0),
            )
            continue

        by_tier_raw = band_data.get("by_tier") or {}
        by_tier = {
            str(tier): int(tier_count or 0)
            for tier, tier_count in by_tier_raw.items()
        }
        bands[name] = {
            "band": name,
            "count": count,
            "unscanned": 0,
            "by_tier": by_tier,
        }

    normalized_bands: List[ScanCountBand] = []
    for name in _BAND_ORDER:
        normalized_bands.append(
            ScanCountBand(
                **bands.get(
                    name,
                    {
                        "band": name,
                        "count": 0,
                        "unscanned": 0,
                        "by_tier": {},
                    },
                )
            )
        )

    calculated_total = sum(band.count for band in normalized_bands) + unscanned_total
    total_servers = int(data.get("total_servers") or calculated_total)

    return ScanCountHistogramResponse(
        bands=normalized_bands,
        total_servers=total_servers,
        unscanned_total=unscanned_total,
    )


@router.get(
    "/registry/scan-count-histogram",
    response_model=ScanCountHistogramResponse,
)
def scan_count_histogram(
    db: Session = Depends(get_session),
) -> ScanCountHistogramResponse:
    return _normalize_response(get_scan_count_histogram(db))


if __name__ == "__main__":
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

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    risk_tier TEXT,
                    scan_count INTEGER,
                    confidence FLOAT,
                    description TEXT,
                    first_seen DATETIME,
                    last_assessed DATETIME,
                    last_scanned DATETIME,
                    last_seen DATETIME,
                    meta TEXT,
                    registry_source TEXT,
                    trust_score FLOAT,
                    url TEXT,
                    verdict TEXT,
                    verdict_reasoning TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, risk_tier, scan_count)
                VALUES
                    ('server-0', 'Server 0', 'HIGH_RISK_ISOLATED', 0),
                    ('server-1', 'Server 1', 'STANDARD', 1),
                    ('server-2', 'Server 2', 'HIGH_RISK_ISOLATED', 4),
                    ('server-3', 'Server 3', 'STANDARD', 60),
                    ('server-4', 'Server 4', 'HIGH_RISK_ISOLATED', NULL)
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/scan-count-histogram")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert len(payload["bands"]) == 5, payload
        assert payload["unscanned_total"] == 1, payload
        assert payload["total_servers"] == 5, payload
        assert {band["band"] for band in payload["bands"]} == set(_BAND_ORDER)

    print("PASS")