from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api", tags=["confidence"])


class ConfidenceBand(BaseModel):
    band: str
    min: Optional[float] = None
    max: Optional[float] = None
    count: int
    servers: List[Dict[str, Any]]


class ConfidenceBandResponse(BaseModel):
    bands: List[ConfidenceBand]
    total: int
    mean_confidence: float


NUMERIC_BANDS = [
    ("0.0-0.2", 0.0, 0.2),
    ("0.2-0.4", 0.2, 0.4),
    ("0.4-0.6", 0.4, 0.6),
    ("0.6-0.8", 0.6, 0.8),
    ("0.8-1.0", 0.8, 1.0),
]


def _band_index(value: float) -> int:
    if value < 0.2:
        return 0
    if value < 0.4:
        return 1
    if value < 0.6:
        return 2
    if value < 0.8:
        return 3
    return 4


@router.get(
    "/confidence/band-distribution",
    response_model=ConfidenceBandResponse,
)
def get_confidence_band_distribution(
    registry_source: Optional[str] = None,
    session: Session = Depends(get_session),
) -> ConfidenceBandResponse:
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.confidence,
        McpServerRegistry.registry_source,
    )
    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )
    statement = statement.order_by(McpServerRegistry.server_id)

    rows = session.execute(statement).mappings().all()

    grouped: Dict[str, List[Dict[str, Any]]] = {
        label: [] for label, _, _ in NUMERIC_BANDS
    }
    grouped["unknown"] = []
    confidence_values: List[float] = []

    for row in rows:
        raw_confidence = row["confidence"]
        server = {
            "server_id": row["server_id"],
            "confidence": (
                float(raw_confidence) if raw_confidence is not None else None
            ),
            "registry_source": row["registry_source"],
        }

        if raw_confidence is None:
            grouped["unknown"].append(server)
            continue

        confidence = float(raw_confidence)
        confidence_values.append(confidence)
        grouped[NUMERIC_BANDS[_band_index(confidence)][0]].append(server)

    bands: List[ConfidenceBand] = []
    for label, minimum, maximum in NUMERIC_BANDS:
        servers = grouped[label]
        bands.append(
            ConfidenceBand(
                band=label,
                min=minimum,
                max=maximum,
                count=len(servers),
                servers=servers,
            )
        )

    unknown_servers = grouped["unknown"]
    bands.append(
        ConfidenceBand(
            band="unknown",
            min=None,
            max=None,
            count=len(unknown_servers),
            servers=unknown_servers,
        )
    )

    mean_confidence = (
        sum(confidence_values) / len(confidence_values)
        if confidence_values
        else 0.0
    )

    return ConfidenceBandResponse(
        bands=bands,
        total=len(rows),
        mean_confidence=mean_confidence,
    )


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
    test_session = sessionmaker(bind=engine)()

    values = [0.0, 0.25, 0.5, 0.75, 1.0, None]
    for index, value in enumerate(values, start=1):
        test_session.add(
            McpServerRegistry(
                server_id=f"server-{index}",
                confidence=value,
                registry_source="self-test",
            )
        )
    test_session.commit()

    def override_get_session():
        yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/confidence/band-distribution")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 6
            numeric_bands = [
                band for band in payload["bands"] if band["band"] != "unknown"
            ]
            unknown_bands = [
                band for band in payload["bands"] if band["band"] == "unknown"
            ]
            assert len(numeric_bands) == 5
            assert all(band["count"] >= 1 for band in numeric_bands)
            assert len(unknown_bands) == 1
            assert unknown_bands[0]["count"] == 1
    finally:
        test_session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()