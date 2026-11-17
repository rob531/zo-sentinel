from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class AgeBucket(BaseModel):
    bucket: str
    count: int


class AgeDistributionResponse(BaseModel):
    total: int
    buckets: list[AgeBucket]
    stale_count: int
    timestamp_source: str


ROUTER_PREFIX = "/api"
ENDPOINT_PATH = "/attestations/age-distribution"
FALLBACK_TIMESTAMP_SOURCE = "mcp_server_registry.last_assessed"
BUCKET_NAMES = ("0-1d", "1-7d", "7-30d", "30-90d", "90d+")


def _table_columns(session: Session, table_name: str) -> set[str]:
    try:
        return {
            column["name"]
            for column in inspect(session.get_bind()).get_columns(table_name)
        }
    except Exception:
        return set()


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError:
        for format_string in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                parsed = datetime.strptime(text_value, format_string)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _age_in_days(value: Any, now: datetime) -> Optional[float]:
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        return None
    return max(0.0, (now - timestamp).total_seconds() / 86400.0)


def _bucket_for_age(age_days: float) -> str:
    if age_days <= 1:
        return "0-1d"
    if age_days <= 7:
        return "1-7d"
    if age_days <= 30:
        return "7-30d"
    if age_days <= 90:
        return "30-90d"
    return "90d+"


def _registry_timestamps(session: Session) -> dict[Any, Any]:
    timestamps: dict[Any, Any] = {}
    for server in session.query(McpServerRegistry).all():
        timestamps[server.server_id] = server.last_assessed
    return timestamps


def _fallback_attestation_rows(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            "SELECT id, server_id, created_at, issued_at "
            "FROM mcp_attestations"
        )
    ).mappings().all()

    registry_timestamps = _registry_timestamps(session)
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "server_id": row["server_id"],
                "timestamp": registry_timestamps.get(row["server_id"]),
            }
        )
    return result


def _registry_rows(session: Session) -> list[dict[str, Any]]:
    return [
        {
            "id": server.server_id,
            "server_id": server.server_id,
            "timestamp": server.last_assessed,
        }
        for server in session.query(McpServerRegistry).all()
    ]


def get_age_distribution(
    session: Session = Depends(get_session),
) -> AgeDistributionResponse:
    columns = _table_columns(session, "mcp_attestations")
    timestamp_column = next(
        (column for column in ("created_at", "issued_at") if column in columns),
        None,
    )

    if not columns:
        rows = _registry_rows(session)
        timestamp_source = FALLBACK_TIMESTAMP_SOURCE
    elif timestamp_column is None:
        rows = _fallback_attestation_rows(session)
        timestamp_source = FALLBACK_TIMESTAMP_SOURCE
    else:
        rows = session.execute(
            text(
                f"SELECT id, server_id, {timestamp_column} AS timestamp "
                "FROM mcp_attestations"
            )
        ).mappings().all()
        timestamp_source = f"mcp_attestations.{timestamp_column}"

    now = datetime.now(timezone.utc)
    counts = {bucket: 0 for bucket in BUCKET_NAMES}
    stale_count = 0

    for row in rows:
        age_days = _age_in_days(row["timestamp"], now)
        if age_days is None:
            continue
        counts[_bucket_for_age(age_days)] += 1
        if age_days > 30:
            stale_count += 1

    return AgeDistributionResponse(
        total=len(rows),
        buckets=[AgeBucket(bucket=bucket, count=counts[bucket]) for bucket in BUCKET_NAMES],
        stale_count=stale_count,
        timestamp_source=timestamp_source,
    )


def create_app() -> FastAPI:
    application = FastAPI()
    application.get(
        f"{ROUTER_PREFIX}{ENDPOINT_PATH}",
        response_model=AgeDistributionResponse,
        name="get_attestation_age_distribution",
    )(get_age_distribution)
    return application


app = create_app()


if __name__ == "__main__":
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE mcp_attestations ("
                "id INTEGER PRIMARY KEY, "
                "server_id TEXT, "
                "created_at TEXT"
                ")"
            )
        )

    test_session = TestSession()
    now = datetime.now(timezone.utc)
    test_session.execute(
        text(
            "INSERT INTO mcp_attestations "
            "(id, server_id, created_at) VALUES "
            "(:id, :server_id, :created_at)"
        ),
        [
            {
                "id": index,
                "server_id": f"server-{index}",
                "created_at": (now - timedelta(days=age)).isoformat(),
            }
            for index, age in enumerate((0, 3, 10, 45, 120), start=1)
        ],
    )
    test_session.commit()

    test_app = FastAPI()
    test_app.get(
        f"{ROUTER_PREFIX}{ENDPOINT_PATH}",
        response_model=AgeDistributionResponse,
    )(get_age_distribution)

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(f"{ROUTER_PREFIX}{ENDPOINT_PATH}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 5
        assert payload["stale_count"] == 2
        assert next(
            bucket["count"]
            for bucket in payload["buckets"]
            if bucket["bucket"] == "0-1d"
        ) == 1

    test_session.close()
    test_engine.dispose()
    print("PASS")