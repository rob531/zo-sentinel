from collections.abc import Generator

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


def _scan_count_bands(session: Session) -> dict:
    totals = session.execute(
        text(
            """
            SELECT
                COUNT(*) AS total_servers,
                COALESCE(SUM(CASE WHEN last_scanned IS NULL THEN 1 ELSE 0 END), 0)
                    AS never_scanned,
                COALESCE(SUM(CASE WHEN risk_tier IS NOT NULL AND scan_count = 0
                                  THEN 1 ELSE 0 END), 0)
                    AS banded_no_scan
            FROM mcp_server_registry
            """
        )
    ).mappings().one()

    band_rows = session.execute(
        text(
            """
            SELECT
                CASE
                    WHEN scan_count IS NULL OR scan_count = 0 THEN '0'
                    WHEN scan_count = 1 THEN '1'
                    WHEN scan_count BETWEEN 2 AND 3 THEN '2-3'
                    WHEN scan_count BETWEEN 4 AND 9 THEN '4-9'
                    ELSE '10+'
                END AS band,
                COUNT(*) AS count
            FROM mcp_server_registry
            GROUP BY
                CASE
                    WHEN scan_count IS NULL OR scan_count = 0 THEN '0'
                    WHEN scan_count = 1 THEN '1'
                    WHEN scan_count BETWEEN 2 AND 3 THEN '2-3'
                    WHEN scan_count BETWEEN 4 AND 9 THEN '4-9'
                    ELSE '10+'
                END
            """
        )
    ).mappings().all()

    source_rows = session.execute(
        text(
            """
            SELECT
                registry_source,
                SUM(CASE WHEN last_scanned IS NULL THEN 1 ELSE 0 END) AS never_scanned
            FROM mcp_server_registry
            GROUP BY registry_source
            ORDER BY registry_source
            """
        )
    ).mappings().all()

    band_order = {"0": 0, "1": 1, "2-3": 2, "4-9": 3, "10+": 4}
    counts = {band: 0 for band in band_order}
    for row in band_rows:
        counts[row["band"]] = row["count"]

    return {
        "total_servers": totals["total_servers"],
        "never_scanned": totals["never_scanned"],
        "banded_no_scan": totals["banded_no_scan"],
        "bands": [
            {"band": band, "count": counts[band]}
            for band in ("0", "1", "2-3", "4-9", "10+")
        ],
        "by_source": [
            {
                "registry_source": row["registry_source"],
                "never_scanned": row["never_scanned"],
            }
            for row in source_rows
        ],
    }


def create_test_app() -> FastAPI:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = SessionLocal()
    try:
        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, trust_score, verdict,
                     risk_tier, scan_count, first_seen, last_seen, last_scanned,
                     last_assessed)
                VALUES
                    (:server_id, :name, :registry_source, :trust_score, :verdict,
                     :risk_tier, :scan_count, :first_seen, :last_seen, :last_scanned,
                     :last_assessed)
                """
            ),
            [
                {
                    "server_id": "server-1",
                    "name": "server one",
                    "registry_source": "alpha",
                    "trust_score": 0.9,
                    "verdict": "approved",
                    "risk_tier": "high",
                    "scan_count": 0,
                    "first_seen": None,
                    "last_seen": None,
                    "last_scanned": None,
                    "last_assessed": None,
                },
                {
                    "server_id": "server-2",
                    "name": "server two",
                    "registry_source": "beta",
                    "trust_score": 0.8,
                    "verdict": "approved",
                    "risk_tier": None,
                    "scan_count": 0,
                    "first_seen": None,
                    "last_seen": None,
                    "last_scanned": "2024-01-01T00:00:00",
                    "last_assessed": None,
                },
                {
                    "server_id": "server-3",
                    "name": "server three",
                    "registry_source": "alpha",
                    "trust_score": 0.7,
                    "verdict": "approved",
                    "risk_tier": "medium",
                    "scan_count": 1,
                    "first_seen": None,
                    "last_seen": None,
                    "last_scanned": "2024-01-01T00:00:00",
                    "last_assessed": None,
                },
                {
                    "server_id": "server-4",
                    "name": "server four",
                    "registry_source": "beta",
                    "trust_score": 0.6,
                    "verdict": "approved",
                    "risk_tier": "low",
                    "scan_count": 4,
                    "first_seen": None,
                    "last_seen": None,
                    "last_scanned": "2024-01-01T00:00:00",
                    "last_assessed": None,
                },
                {
                    "server_id": "server-5",
                    "name": "server five",
                    "registry_source": "alpha",
                    "trust_score": 0.5,
                    "verdict": "approved",
                    "risk_tier": "low",
                    "scan_count": 12,
                    "first_seen": None,
                    "last_seen": None,
                    "last_scanned": "2024-01-01T00:00:00",
                    "last_assessed": None,
                },
            ],
        )
        session.commit()
    finally:
        session.close()

    def override_get_session() -> Generator[Session, None, None]:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()

    @test_app.get("/api/registry/scan-count-bands")
    def scan_count_bands(session: Session = Depends(get_session)) -> dict:
        return _scan_count_bands(session)

    test_app.dependency_overrides[get_session] = override_get_session
    return test_app


def main() -> None:
    test_app = create_test_app()
    with TestClient(test_app) as client:
        response = client.get("/api/registry/scan-count-bands")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["total_servers"] == 5
        assert payload["never_scanned"] == 1
        assert payload["banded_no_scan"] == 1
        assert next(
            band["count"] for band in payload["bands"] if band["band"] == "0"
        ) == 2

    print("PASS")


if __name__ == "__main__":
    main()