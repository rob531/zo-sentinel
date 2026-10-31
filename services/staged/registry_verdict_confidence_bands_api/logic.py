from collections import defaultdict
from typing import Dict, List, Optional, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class ConfidenceBandResponse(BaseModel):
    band: str
    count: int
    verdict_mix: Dict[str, int] = Field(default_factory=dict)


class ConfidenceTotalsResponse(BaseModel):
    servers: int
    scored: int
    null_confidence: int


class RegistryConfidenceGroupResponse(BaseModel):
    registry_source: str
    bands: List[ConfidenceBandResponse]
    totals: ConfidenceTotalsResponse


class RegistryVerdictConfidenceBandsResponse(BaseModel):
    bands: List[ConfidenceBandResponse]
    totals: ConfidenceTotalsResponse


class RegistryVerdictConfidenceBandsGroupedResponse(BaseModel):
    groups: List[RegistryConfidenceGroupResponse]


router = APIRouter()

_BANDS = (
    (0.0, 0.2, "0.0-0.2"),
    (0.2, 0.4, "0.2-0.4"),
    (0.4, 0.6, "0.4-0.6"),
    (0.6, 0.8, "0.6-0.8"),
    (0.8, 1.0, "0.8-1.0"),
)


def _empty_bands() -> List[ConfidenceBandResponse]:
    return [
        ConfidenceBandResponse(band=label, count=0, verdict_mix={})
        for _, _, label in _BANDS
    ]


def _confidence_band(confidence: float) -> Optional[str]:
    for lower, upper, label in _BANDS:
        if confidence == 1.0 and label == "0.8-1.0":
            return label
        if lower <= confidence < upper:
            return label
    return None


def _build_band_data(rows) -> Dict[str, object]:
    bands = {band.band: band for band in _empty_bands()}
    scored = 0
    null_confidence = 0

    for row in rows:
        confidence = row.confidence
        if confidence is None:
            null_confidence += 1
            continue

        scored += 1
        band_label = _confidence_band(float(confidence))
        if band_label is None:
            continue

        band = bands[band_label]
        band.count += 1
        if row.verdict is not None:
            verdict = str(row.verdict)
            band.verdict_mix[verdict] = band.verdict_mix.get(verdict, 0) + 1

    return {
        "bands": list(bands.values()),
        "totals": {
            "servers": len(rows),
            "scored": scored,
            "null_confidence": null_confidence,
        },
    }


def build_registry_verdict_confidence_bands(
    rows,
    registry_source: Optional[str] = None,
) -> Union[
    RegistryVerdictConfidenceBandsResponse,
    RegistryVerdictConfidenceBandsGroupedResponse,
]:
    if registry_source is None:
        data = _build_band_data(rows)
        return RegistryVerdictConfidenceBandsResponse(**data)

    grouped = defaultdict(list)
    for row in rows:
        source = row.registry_source
        if source is None:
            source = ""
        grouped[str(source)].append(row)

    groups = []
    for source in sorted(grouped):
        data = _build_band_data(grouped[source])
        groups.append(
            RegistryConfidenceGroupResponse(
                registry_source=source,
                bands=data["bands"],
                totals=data["totals"],
            )
        )

    return RegistryVerdictConfidenceBandsGroupedResponse(groups=groups)


@router.get(
    "/api/registry/verdict-confidence-bands",
    response_model=Union[
        RegistryVerdictConfidenceBandsResponse,
        RegistryVerdictConfidenceBandsGroupedResponse,
    ],
)
def get_registry_verdict_confidence_bands(
    registry_source: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_session),
):
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.registry_source,
        McpServerRegistry.verdict,
        McpServerRegistry.confidence,
    ).order_by(McpServerRegistry.server_id)

    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    statement = statement.limit(limit)
    rows = session.execute(statement).all()

    return build_registry_verdict_confidence_bands(
        rows,
        registry_source=registry_source,
    )


if __name__ == "__main__":
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
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="confidence-test-1",
                    name="Confidence Test 1",
                    registry_source="test-source",
                    verdict="TRUSTED_GENERAL",
                    confidence=0.1,
                ),
                McpServerRegistry(
                    server_id="confidence-test-2",
                    name="Confidence Test 2",
                    registry_source="test-source",
                    verdict="RISKY",
                    confidence=0.35,
                ),
                McpServerRegistry(
                    server_id="confidence-test-3",
                    name="Confidence Test 3",
                    registry_source="test-source",
                    verdict="TRUSTED_GENERAL",
                    confidence=0.5,
                ),
                McpServerRegistry(
                    server_id="confidence-test-4",
                    name="Confidence Test 4",
                    registry_source="test-source",
                    verdict="RISKY",
                    confidence=0.9,
                ),
                McpServerRegistry(
                    server_id="confidence-test-5",
                    name="Confidence Test 5",
                    registry_source="test-source",
                    verdict="TRUSTED_GENERAL",
                    confidence=None,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/registry/verdict-confidence-bands",
            params={"registry_source": "test-source"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["groups"]) == 1
        group = payload["groups"][0]
        assert len(group["bands"]) == 5
        top_band = next(
            band for band in group["bands"] if band["band"] == "0.8-1.0"
        )
        assert top_band["count"] == 1
        assert group["totals"]["null_confidence"] == 1

    print("PASS")