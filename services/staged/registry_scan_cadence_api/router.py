from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

if __package__:
    from .logic import get_scan_cadence
else:
    from logic import get_scan_cadence


class RegistrySourceCadence(BaseModel):
    registry_source: str
    servers: int
    never_scanned: int
    mean_scan_count: float
    median_days_since_scan: Optional[float]


class RegistryScanCadenceTotals(BaseModel):
    servers: int
    never_scanned: int
    sources: int


class RegistryScanCadenceResponse(BaseModel):
    sources: List[RegistrySourceCadence]
    totals: RegistryScanCadenceTotals


router = APIRouter()


@router.get(
    "/registry/scan-cadence",
    response_model=RegistryScanCadenceResponse,
)
def registry_scan_cadence(
    session: Session = Depends(get_session),
) -> RegistryScanCadenceResponse:
    return get_scan_cadence(session)


if __name__ == "__main__":
    from datetime import datetime, timedelta

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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    description VARCHAR,
                    url VARCHAR,
                    registry_source VARCHAR,
                    scan_count INTEGER,
                    last_scanned DATETIME,
                    last_seen DATETIME,
                    first_seen DATETIME,
                    verdict VARCHAR,
                    verdict_reasoning VARCHAR,
                    confidence FLOAT,
                    trust_score FLOAT,
                    risk_tier VARCHAR,
                    last_assessed DATETIME,
                    meta JSON
                )
                """
            )
        )

        now = datetime.utcnow()
        rows = [
            ("server-1", "never-scanned", "source-never", 0, None),
            ("server-2", "old-one", "source-old", 2, now - timedelta(days=10)),
            ("server-3", "old-two", "source-old", 4, now - timedelta(days=20)),
            ("server-4", "old-three", "source-old", 6, now - timedelta(days=30)),
        ]
        for server_id, name, source, scan_count, last_scanned in rows:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry (
                        server_id,
                        name,
                        registry_source,
                        scan_count,
                        last_scanned,
                        last_seen,
                        first_seen,
                        verdict
                    ) VALUES (
                        :server_id,
                        :name,
                        :registry_source,
                        :scan_count,
                        :last_scanned,
                        :last_seen,
                        :first_seen,
                        'approved'
                    )
                    """
                ),
                {
                    "server_id": server_id,
                    "name": name,
                    "registry_source": source,
                    "scan_count": scan_count,
                    "last_scanned": (
                        last_scanned.isoformat() if last_scanned is not None else None
                    ),
                    "last_seen": now.isoformat(),
                    "first_seen": (now - timedelta(days=90)).isoformat(),
                },
            )

    def override_get_session():
        with testing_session() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/scan-cadence")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert len(payload["sources"]) == 2
    assert payload["totals"]["servers"] == 4
    assert payload["totals"]["never_scanned"] == 1
    assert payload["totals"]["sources"] == 2

    old_source = next(
        source
        for source in payload["sources"]
        if source["registry_source"] == "source-old"
    )
    assert old_source["servers"] == 3
    assert old_source["never_scanned"] == 0
    assert old_source["mean_scan_count"] == 4.0
    assert abs(old_source["median_days_since_scan"] - 20.0) < 0.1

    print("PASS")