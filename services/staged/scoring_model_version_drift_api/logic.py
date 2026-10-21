from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class ModelVersionEntry(BaseModel):
    decision_rule_version: Optional[str] = None
    model_version: Optional[str] = None
    adapter_sha256: Optional[str] = None
    row_count: int
    server_count: int
    latest_scored_at: Optional[str] = None
    first_scored_at: Optional[str] = None
    is_current: bool


class CurrentModelVersion(BaseModel):
    decision_rule_version: Optional[str] = None
    model_version: Optional[str] = None
    adapter_sha256: Optional[str] = None


class ModelVersionDriftResponse(BaseModel):
    days: int
    versions: List[ModelVersionEntry]
    current_version: CurrentModelVersion
    stale_server_count: int


router = APIRouter(prefix="/api")


def _timestamp_value(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            return value
    return value


def _timestamp_key(value):
    timestamp = _timestamp_value(value)
    if isinstance(timestamp, datetime):
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.timestamp()
    if isinstance(timestamp, date):
        return datetime(
            timestamp.year, timestamp.month, timestamp.day, tzinfo=timezone.utc
        ).timestamp()
    return str(timestamp)


def _iso_timestamp(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).isoformat()
    return str(value)


def _version_value(value) -> Optional[str]:
    if value is None:
        return None
    return str(value)


@router.get(
    "/scoring/model-version-drift",
    response_model=ModelVersionDriftResponse,
)
def model_version_drift(
    days: int = Query(30),
    session: Session = Depends(get_session),
) -> ModelVersionDriftResponse:
    days = max(1, min(365, int(days)))

    score_rows = session.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.scored_at,
        )
    ).all()

    registry_rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
        )
    ).all()
    registry_names = {
        row.server_id: row.name
        for row in registry_rows
        if row.server_id is not None
    }

    grouped: Dict[
        Tuple[Optional[str], Optional[str], Optional[str]],
        Dict[str, object],
    ] = {}

    latest_by_server: Dict[object, Tuple[float, object]] = {}
    fleet_latest_key = None
    current_row = None

    for row in score_rows:
        decision_rule_version = _version_value(row.decision_rule_version)
        model_version = _version_value(row.model_version)
        adapter_sha256 = _version_value(row.adapter_sha256)
        version_key = (
            decision_rule_version,
            model_version,
            adapter_sha256,
        )

        if version_key not in grouped:
            grouped[version_key] = {
                "row_count": 0,
                "server_ids": set(),
                "latest_scored_at": None,
                "first_scored_at": None,
                "latest_key": None,
            }

        entry = grouped[version_key]
        entry["row_count"] = int(entry["row_count"]) + 1
        if row.server_id is not None:
            entry["server_ids"].add(row.server_id)

        scored_key = _timestamp_key(row.scored_at)
        if entry["latest_key"] is None or scored_key > entry["latest_key"]:
            entry["latest_key"] = scored_key
            entry["latest_scored_at"] = row.scored_at
        if entry["first_scored_at"] is None:
            entry["first_scored_at"] = row.scored_at
        elif scored_key < _timestamp_key(entry["first_scored_at"]):
            entry["first_scored_at"] = row.scored_at

        if fleet_latest_key is None or scored_key > fleet_latest_key:
            fleet_latest_key = scored_key
            current_row = row

        if row.server_id is not None:
            previous = latest_by_server.get(row.server_id)
            if previous is None or scored_key > previous[0]:
                latest_by_server[row.server_id] = (scored_key, row.scored_at)

    current_version = (
        _version_value(current_row.decision_rule_version),
        _version_value(current_row.model_version),
        _version_value(current_row.adapter_sha256),
    ) if current_row is not None else (None, None, None)

    versions = []
    for version_key, entry in grouped.items():
        versions.append(
            ModelVersionEntry(
                decision_rule_version=version_key[0],
                model_version=version_key[1],
                adapter_sha256=version_key[2],
                row_count=int(entry["row_count"]),
                server_count=len(entry["server_ids"]),
                latest_scored_at=_iso_timestamp(entry["latest_scored_at"]),
                first_scored_at=_iso_timestamp(entry["first_scored_at"]),
                is_current=version_key == current_version,
            )
        )

    versions.sort(
        key=lambda item: _timestamp_key(item.latest_scored_at),
        reverse=True,
    )

    stale_server_count = 0
    if fleet_latest_key is not None:
        stale_server_count = sum(
            1
            for latest_key, _latest_value in latest_by_server.values()
            if latest_key < fleet_latest_key
        )

    return ModelVersionDriftResponse(
        days=days,
        versions=versions,
        current_version=CurrentModelVersion(
            decision_rule_version=current_version[0],
            model_version=current_version[1],
            adapter_sha256=current_version[2],
        ),
        stale_server_count=stale_server_count,
    )


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR,
                    axis_name VARCHAR,
                    decision_rule_version VARCHAR,
                    model_version VARCHAR,
                    adapter_sha256 VARCHAR,
                    scored_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR
                )
                """
            )
        )

    test_session_factory = sessionmaker(bind=test_engine)
    test_session = test_session_factory()

    with test_session.begin():
        test_session.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name)
                VALUES (:server_id, :name)
                """
            ),
            [
                {"server_id": "server-a", "name": "Server A"},
                {"server_id": "server-b", "name": "Server B"},
                {"server_id": "server-c", "name": "Server C"},
            ],
        )

        rows = []
        for server_id, rule_version, scored_at in [
            ("server-a", "rule-v3", "2024-05-01T00:00:00"),
            ("server-b", "rule-v3", "2024-05-01T00:00:00"),
            ("server-c", "rule-v4", "2024-06-01T00:00:00"),
        ]:
            for axis_name in ("axis-1", "axis-2"):
                rows.append(
                    {
                        "server_id": server_id,
                        "axis_name": axis_name,
                        "decision_rule_version": rule_version,
                        "model_version": "model-v1",
                        "adapter_sha256": f"sha-{rule_version}",
                        "scored_at": scored_at,
                    }
                )

        test_session.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    server_id,
                    axis_name,
                    decision_rule_version,
                    model_version,
                    adapter_sha256,
                    scored_at
                ) VALUES (
                    :server_id,
                    :axis_name,
                    :decision_rule_version,
                    :model_version,
                    :adapter_sha256,
                    :scored_at
                )
                """
            ),
            rows,
        )

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        return test_session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/scoring/model-version-drift",
        params={"days": 30},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["days"] == 30
    assert len(payload["versions"]) == 2
    assert payload["current_version"]["decision_rule_version"] == "rule-v4"
    assert payload["stale_server_count"] == 2
    assert all(entry["row_count"] >= 1 for entry in payload["versions"])

    test_session.close()
    test_engine.dispose()
    print("PASS")