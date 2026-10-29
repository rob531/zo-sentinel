from __future__ import annotations

from datetime import date, datetime, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class RegistrySourceCadence(BaseModel):
    registry_source: str
    servers: int = Field(ge=0)
    never_scanned: int = Field(ge=0)
    mean_scan_count: float = Field(ge=0)
    median_days_since_scan: Optional[float]


class ScanCadenceTotals(BaseModel):
    servers: int = Field(ge=0)
    never_scanned: int = Field(ge=0)
    sources: int = Field(ge=0)


class ScanCadenceResponse(BaseModel):
    sources: List[RegistrySourceCadence]
    totals: ScanCadenceTotals


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith(("Z", "z")):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _median_days_since_scan(values: Iterable[Any], now: datetime) -> Optional[float]:
    timestamps = [_parse_timestamp(value) for value in values]
    ages = [
        (now - timestamp).total_seconds() / 86400.0
        for timestamp in timestamps
        if timestamp is not None
    ]
    if not ages:
        return None
    return float(median(ages))


def _mean_scan_count(values: Iterable[Any]) -> float:
    counts = [float(value) for value in values if value is not None]
    if not counts:
        return 0.0
    return sum(counts) / len(counts)


def _scan_cadence(session=Depends(get_session)) -> ScanCadenceResponse:
    table_name = McpServerRegistry.__tablename__
    if table_name != "mcp_server_registry":
        raise RuntimeError(
            f"MCPServerRegistry must map to mcp_server_registry, got {table_name!r}"
        )

    rows = session.execute(
        text(
            """
            SELECT
                registry_source,
                scan_count,
                last_scanned,
                last_seen,
                first_seen,
                verdict
            FROM mcp_server_registry
            """
        )
    ).mappings().all()

    now = datetime.now(timezone.utc)
    grouped: Dict[str, List[Any]] = {}

    for row in rows:
        source = row["registry_source"]
        if source is None:
            continue
        grouped.setdefault(str(source), []).append(row)

    sources: List[RegistrySourceCadence] = []
    total_servers = 0
    total_never_scanned = 0

    for registry_source in sorted(grouped):
        source_rows = grouped[registry_source]
        scan_counts = [row["scan_count"] for row in source_rows]
        never_scanned = sum(1 for count in scan_counts if count == 0)
        servers = len(source_rows)

        sources.append(
            RegistrySourceCadence(
                registry_source=registry_source,
                servers=servers,
                never_scanned=never_scanned,
                mean_scan_count=_mean_scan_count(scan_counts),
                median_days_since_scan=_median_days_since_scan(
                    (row["last_scanned"] for row in source_rows), now
                ),
            )
        )

        total_servers += servers
        total_never_scanned += never_scanned

    return ScanCadenceResponse(
        sources=sources,
        totals=ScanCadenceTotals(
            servers=total_servers,
            never_scanned=total_never_scanned,
            sources=len(sources),
        ),
    )


def _build_test_app():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    id INTEGER PRIMARY KEY,
                    registry_source VARCHAR NOT NULL,
                    scan_count INTEGER NOT NULL DEFAULT 0,
                    last_scanned TIMESTAMP,
                    last_seen TIMESTAMP,
                    first_seen TIMESTAMP,
                    verdict VARCHAR
                )
                """
            )
        )

        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (registry_source, scan_count, last_scanned, last_seen, first_seen, verdict)
                VALUES
                    (:registry_source, :scan_count, :last_scanned, :last_seen, :first_seen, :verdict)
                """
            ),
            [
                {
                    "registry_source": "alpha",
                    "scan_count": 0,
                    "last_scanned": None,
                    "last_seen": None,
                    "first_seen": "2023-01-01T00:00:00+00:00",
                    "verdict": "pending",
                },
                {
                    "registry_source": "alpha",
                    "scan_count": 3,
                    "last_scanned": None,
                    "last_seen": None,
                    "first_seen": "2023-01-02T00:00:00+00:00",
                    "verdict": "known",
                },
                {
                    "registry_source": "beta",
                    "scan_count": 2,
                    "last_scanned": "2020-01-01T00:00:00+00:00",
                    "last_seen": "2020-01-01T00:00:00+00:00",
                    "first_seen": "2019-01-01T00:00:00+00:00",
                    "verdict": "known",
                },
                {
                    "registry_source": "beta",
                    "scan_count": 4,
                    "last_scanned": "2020-01-11T00:00:00+00:00",
                    "last_seen": "2020-01-11T00:00:00+00:00",
                    "first_seen": "2019-02-01T00:00:00+00:00",
                    "verdict": "known",
                },
            ],
        )

    app = FastAPI()
    app.get("/api/registry/scan-cadence", response_model=ScanCadenceResponse)(
        _scan_cadence
    )

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    return app, engine


def _run_self_test() -> Dict[str, Any]:
    app, engine = _build_test_app()
    reference_time = datetime.now(timezone.utc)

    try:
        with TestClient(app) as client:
            response = client.get("/api/registry/scan-cadence")

        assert response.status_code == 200, response.text
        payload = response.json()

        assert len(payload["sources"]) == 2, payload
        assert payload["totals"] == {
            "servers": 4,
            "never_scanned": 1,
            "sources": 2,
        }, payload

        by_source = {
            source["registry_source"]: source for source in payload["sources"]
        }

        assert by_source["alpha"]["servers"] == 2, payload
        assert by_source["alpha"]["never_scanned"] == 1, payload
        assert by_source["alpha"]["mean_scan_count"] == 1.5, payload
        assert by_source["alpha"]["median_days_since_scan"] is None, payload

        assert by_source["beta"]["servers"] == 2, payload
        assert by_source["beta"]["never_scanned"] == 0, payload
        assert by_source["beta"]["mean_scan_count"] == 3.0, payload

        first_timestamp = _parse_timestamp("2020-01-01T00:00:00+00:00")
        second_timestamp = _parse_timestamp("2020-01-11T00:00:00+00:00")
        assert first_timestamp is not None
        assert second_timestamp is not None

        expected_median = (
            (reference_time - first_timestamp).total_seconds()
            + (reference_time - second_timestamp).total_seconds()
        ) / 2.0 / 86400.0
        actual_median = by_source["beta"]["median_days_since_scan"]

        assert actual_median is not None, payload
        assert abs(actual_median - expected_median) < 0.001, (
            actual_median,
            expected_median,
        )

        print("PASS")
        return payload
    finally:
        engine.dispose()


def run() -> Dict[str, Any]:
    return _run_self_test()


if __name__ == "__main__":
    run()