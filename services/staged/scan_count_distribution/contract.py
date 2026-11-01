from datetime import datetime
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry
from services.staged.scan_count_distribution.router import router


def _create_schema(engine: Engine) -> None:
    table_name = McpServerRegistry.__tablename__
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE {table_name} (
                    server_id VARCHAR PRIMARY KEY NOT NULL,
                    name VARCHAR,
                    description TEXT,
                    url TEXT,
                    registry_source VARCHAR,
                    scan_count INTEGER DEFAULT 0,
                    last_scanned DATETIME,
                    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_seen DATETIME,
                    last_assessed DATETIME,
                    confidence FLOAT,
                    trust_score FLOAT,
                    risk_tier VARCHAR,
                    verdict VARCHAR,
                    verdict_reasoning TEXT,
                    meta TEXT
                )
                """
            )
        )


def _seed_rows(session: Session) -> None:
    table_name = McpServerRegistry.__tablename__
    rows = [
        ("server-null", "alpha", None, None, datetime(2024, 1, 1)),
        ("server-zero", "alpha", 0, None, datetime(2024, 1, 2)),
        ("server-one", "beta", 1, datetime(2024, 1, 3), datetime(2024, 1, 3)),
        ("server-three", "beta", 3, datetime(2024, 1, 4), datetime(2024, 1, 4)),
        ("server-five", "alpha", 5, datetime(2024, 1, 5), datetime(2024, 1, 5)),
        ("server-twelve", "beta", 12, datetime(2024, 1, 6), datetime(2024, 1, 6)),
    ]
    session.execute(
        text(
            f"""
            INSERT INTO {table_name}
                (server_id, registry_source, scan_count, last_scanned, first_seen)
            VALUES
                (:server_id, :registry_source, :scan_count, :last_scanned, :first_seen)
            """
        ),
        [
            {
                "server_id": server_id,
                "registry_source": registry_source,
                "scan_count": scan_count,
                "last_scanned": last_scanned,
                "first_seen": first_seen,
            }
            for server_id, registry_source, scan_count, last_scanned, first_seen in rows
        ],
    )
    session.commit()


def run_contract_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    _create_schema(engine)
    seed_session = session_factory()
    try:
        _seed_rows(seed_session)
    finally:
        seed_session.close()

    def override_get_session() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    route_paths = [getattr(route, "path", "") for route in router.routes]
    has_api_prefix = any(path.startswith("/api/") for path in route_paths)
    test_app.include_router(router, prefix="" if has_api_prefix else "/api")
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/registry/scan-count-distribution")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["total"] == 6
            assert sum(bucket["count"] for bucket in payload["buckets"]) == 6

            counts = {bucket["bucket"]: bucket["count"] for bucket in payload["buckets"]}
            assert counts == {"0": 2, "1": 1, "2-3": 1, "4-9": 1, "10+": 1}
            assert payload["never_scanned"] == 2

            by_source = payload["by_source"]
            assert by_source["alpha"]["0"] == 2
            assert by_source["alpha"]["4-9"] == 1
            assert by_source["beta"]["1"] == 1
            assert by_source["beta"]["2-3"] == 1
            assert by_source["beta"]["10+"] == 1
    finally:
        engine.dispose()

    print("PASS")


def _self_test() -> None:
    run_contract_test()


def run_self_test() -> None:
    run_contract_test()


def main() -> int:
    run_contract_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())