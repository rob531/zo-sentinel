from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class ScanCountBand(BaseModel):
    band: str
    lower: int
    upper: Optional[int]
    server_count: int
    pct: float


class ScanCountBandsResponse(BaseModel):
    total_servers: int
    never_scanned: int
    bands: List[ScanCountBand]
    median_scan_count: float


router = APIRouter(prefix="/api")


def build_scan_count_bands(
    session,
    registry_source: Optional[str] = None,
) -> ScanCountBandsResponse:
    query = session.query(
        McpServerRegistry.scan_count,
        McpServerRegistry.last_scanned,
    )
    if registry_source is not None:
        query = query.filter(McpServerRegistry.registry_source == registry_source)

    rows = query.all()
    scan_counts = [int(row.scan_count or 0) for row in rows]
    total_servers = len(scan_counts)

    never_scanned = sum(
        1
        for row in rows
        if int(row.scan_count or 0) == 0 and row.last_scanned is None
    )

    band_specs = (
        ("0", 0, 0),
        ("1", 1, 1),
        ("2", 2, 2),
        ("3-5", 3, 5),
        ("6-10", 6, 10),
        ("11-25", 11, 25),
        ("26+", 26, None),
    )

    bands = []
    for label, lower, upper in band_specs:
        if upper is None:
            count = sum(count >= lower for count in scan_counts)
        else:
            count = sum(lower <= count <= upper for count in scan_counts)
        bands.append(
            ScanCountBand(
                band=label,
                lower=lower,
                upper=upper,
                server_count=count,
                pct=(count / total_servers * 100.0) if total_servers else 0.0,
            )
        )

    if scan_counts:
        ordered = sorted(scan_counts)
        midpoint = len(ordered) // 2
        if len(ordered) % 2:
            median = float(ordered[midpoint])
        else:
            median = (ordered[midpoint - 1] + ordered[midpoint]) / 2.0
    else:
        median = 0.0

    return ScanCountBandsResponse(
        total_servers=total_servers,
        never_scanned=never_scanned,
        bands=bands,
        median_scan_count=median,
    )


@router.get(
    "/registry/scan-count-bands",
    response_model=ScanCountBandsResponse,
)
def get_scan_count_bands(
    registry_source: Optional[str] = None,
    session=Depends(get_session),
) -> ScanCountBandsResponse:
    return build_scan_count_bands(session, registry_source)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        for index, scan_count in enumerate((0, 1, 3, 7, 30), start=1):
            session.add(
                McpServerRegistry(
                    server_id=f"contract-server-{index}",
                    name=f"Contract Server {index}",
                    url=f"https://example.invalid/mcp/{index}",
                    registry_source="contract",
                    scan_count=scan_count,
                    last_scanned=(
                        None
                        if scan_count == 0
                        else datetime(2024, 1, 1, 0, 0, 0)
                    ),
                )
            )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/scan-count-bands")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["total_servers"] == 5
        assert payload["never_scanned"] == 1
        assert payload["median_scan_count"] == 3.0

        bands = {band["band"]: band for band in payload["bands"]}
        assert bands["26+"]["server_count"] == 1
        assert all(0.0 <= band["pct"] <= 100.0 for band in payload["bands"])

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()