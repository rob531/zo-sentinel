from collections.abc import Generator
from typing import Dict, Optional

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_registry_scan_count_bands

router = APIRouter(prefix="/api", tags=["registry"])


class ScanCountBand(BaseModel):
    band: str
    min_scans: int
    max_scans: Optional[int]
    server_count: int
    mean_scan_count: float
    by_source: Dict[str, int]
    by_risk_tier: Dict[str, int]


class NeverRescannedSummary(BaseModel):
    total: int
    by_source: Dict[str, int]


class RegistryScanCountBandResponse(BaseModel):
    bands: list[ScanCountBand]
    never_rescanned: NeverRescannedSummary
    total_servers: int


@router.get(
    "/registry/scan-count-bands",
    response_model=RegistryScanCountBandResponse,
)
def registry_scan_count_bands(
    session: Session = Depends(get_session),
) -> RegistryScanCountBandResponse:
    result = get_registry_scan_count_bands(session)
    return RegistryScanCountBandResponse.model_validate(result)


def _self_test() -> None:
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    rows = [
        ("server-1", "Server One", "registry-a", "high", 0),
        ("server-2", "Server Two", "registry-a", "low", 0),
        ("server-3", "Server Three", "registry-a", "high", 1),
        ("server-4", "Server Four", "registry-b", "low", 3),
        ("server-5", "Server Five", "registry-b", "high", 7),
        ("server-6", "Server Six", "registry-b", "low", 12),
    ]
    with session_factory() as session:
        session.execute(
            McpServerRegistry.__table__.insert(),
            [
                {
                    "server_id": server_id,
                    "name": name,
                    "registry_source": source,
                    "risk_tier": risk_tier,
                    "scan_count": scan_count,
                }
                for server_id, name, source, risk_tier, scan_count in rows
            ],
        )
        session.commit()

    def override_get_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/scan-count-bands")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["bands"]
    zero_band = next(band for band in payload["bands"] if band["band"] == "[0]")
    assert zero_band["server_count"] == 2
    assert payload["total_servers"] == 6

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()