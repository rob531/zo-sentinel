from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import and_, case, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()

BAND_DEFINITIONS = (
    ("0-20", 0, 20),
    ("20-40", 20, 40),
    ("40-60", 40, 60),
    ("60-80", 60, 80),
    ("80-100", 80, 100),
)


class TrustBandServer(BaseModel):
    server_id: str
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None


class TrustBand(BaseModel):
    band: str
    min: int
    max: int
    count: int
    servers: List[TrustBandServer]


class TrustBandResponse(BaseModel):
    total: int
    bands: List[TrustBand]
    unscored_count: int


@router.get("/registry/trust-bands", response_model=TrustBandResponse)
def get_trust_bands(session: Session = Depends(get_session)) -> TrustBandResponse:
    trust_score = McpServerRegistry.trust_score
    bucket = case(
        (and_(trust_score >= 0, trust_score < 20), 0),
        (and_(trust_score >= 20, trust_score < 40), 1),
        (and_(trust_score >= 40, trust_score < 60), 2),
        (and_(trust_score >= 60, trust_score < 80), 3),
        (and_(trust_score >= 80, trust_score <= 100), 4),
        else_=None,
    )

    statement = (
        select(
            bucket.label("band_index"),
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.trust_score,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
            McpServerRegistry.registry_source,
        )
        .order_by(bucket.asc(), McpServerRegistry.server_id.asc())
    )

    rows = session.execute(statement).all()
    servers_by_band = {index: [] for index in range(len(BAND_DEFINITIONS))}
    unscored_count = 0

    for row in rows:
        if row.trust_score is None:
            unscored_count += 1
            continue
        if row.band_index is None:
            continue
        servers_by_band[int(row.band_index)].append(
            TrustBandServer(
                server_id=str(row.server_id),
                verdict=row.verdict,
                risk_tier=row.risk_tier,
                trust_score=float(row.trust_score),
            )
        )

    bands = []
    for index, (label, minimum, maximum) in enumerate(BAND_DEFINITIONS):
        servers = servers_by_band[index]
        bands.append(
            TrustBand(
                band=label,
                min=minimum,
                max=maximum,
                count=len(servers),
                servers=servers,
            )
        )

    return TrustBandResponse(
        total=len(rows),
        bands=bands,
        unscored_count=unscored_count,
    )


get_trust_band_distribution = get_trust_bands


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

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    verdict TEXT,
                    trust_score FLOAT,
                    risk_tier TEXT,
                    last_assessed DATETIME,
                    registry_source TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id,
                    verdict,
                    trust_score,
                    risk_tier,
                    last_assessed,
                    registry_source
                ) VALUES (
                    :server_id,
                    :verdict,
                    :trust_score,
                    :risk_tier,
                    :last_assessed,
                    :registry_source
                )
                """
            ),
            [
                {
                    "server_id": "server-alpha",
                    "verdict": "trusted",
                    "trust_score": 10.0,
                    "risk_tier": "low",
                    "last_assessed": None,
                    "registry_source": "self-test",
                },
                {
                    "server_id": "server-bravo",
                    "verdict": "trusted",
                    "trust_score": 15.0,
                    "risk_tier": "low",
                    "last_assessed": None,
                    "registry_source": "self-test",
                },
                {
                    "server_id": "server-charlie",
                    "verdict": "conditional",
                    "trust_score": 20.0,
                    "risk_tier": "medium",
                    "last_assessed": None,
                    "registry_source": "self-test",
                },
                {
                    "server_id": "server-delta",
                    "verdict": "conditional",
                    "trust_score": 40.0,
                    "risk_tier": "medium",
                    "last_assessed": None,
                    "registry_source": "self-test",
                },
                {
                    "server_id": "server-echo",
                    "verdict": "restricted",
                    "trust_score": 60.0,
                    "risk_tier": "high",
                    "last_assessed": None,
                    "registry_source": "self-test",
                },
                {
                    "server_id": "server-unscored",
                    "verdict": None,
                    "trust_score": None,
                    "risk_tier": None,
                    "last_assessed": None,
                    "registry_source": "self-test",
                },
            ],
        )

    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/trust-bands")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 6
    assert payload["unscored_count"] == 1
    assert payload["bands"][0]["count"] == 2
    assert [
        (band["min"], band["max"]) for band in payload["bands"]
    ] == [
        (0, 20),
        (20, 40),
        (40, 60),
        (60, 80),
        (80, 100),
    ]
    print("PASS")


if __name__ == "__main__":
    _self_test()