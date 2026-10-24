from __future__ import annotations

from datetime import date, datetime, timezone
import json
import math
from typing import Any, Mapping

from fastapi import Depends
from sqlalchemy import MetaData, String, Table, cast, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


AGE_BANDS = (
    ("0-7", 0, 7),
    ("8-30", 8, 30),
    ("31-90", 31, 90),
    ("91+", 91, None),
)

_TIMESTAMP_KEYS = (
    "created_at",
    "createdat",
    "observed_at",
    "observedat",
    "first_observed_at",
    "firstobservedat",
    "first_seen",
    "firstseen",
    "detected_at",
    "detectedat",
    "timestamp",
    "time",
)

_SERVER_ID_KEYS = ("server_id", "serverid", "server")
_SEVERITY_KEYS = ("severity", "severity_level", "risk_level", "level")


def _normalise_key(value: str) -> str:
    return value.replace("-", "_").replace(" ", "_").lower()


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if isinstance(decoded, Mapping):
            return decoded
    return None


def _find_value(value: Any, keys: tuple[str, ...]) -> Any:
    mapping = _as_mapping(value)
    if mapping is None:
        return None

    normalised = {_normalise_key(str(key)): item for key, item in mapping.items()}
    for key in keys:
        if key in normalised:
            return normalised[key]
    return None


def _scalar(value: Any) -> Any:
    mapping = _as_mapping(value)
    if mapping is None:
        return value
    for key in ("value", "level", "name", "rating"):
        if key in mapping:
            return _scalar(mapping[key])
    return value


def _row_payload(row: Mapping[str, Any]) -> Any:
    for key in (
        "payload",
        "data",
        "details",
        "metadata",
        "meta",
        "attributes",
        "association",
        "association_data",
        "threat_data",
    ):
        if key in row:
            mapping = _as_mapping(row[key])
            if mapping is not None:
                return mapping
    return row


def _extract_value(
    row: Mapping[str, Any], keys: tuple[str, ...]
) -> Any:
    direct = _find_value(row, keys)
    if direct is not None:
        return direct
    return _find_value(_row_payload(row), keys)


def _parse_datetime(value: Any) -> datetime | None:
    value = _scalar(value)
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_days(timestamp: datetime, now: datetime) -> float:
    return max(0.0, (now - timestamp).total_seconds() / 86400.0)


def _band_for_age(age: int) -> str:
    for band, lower, upper in AGE_BANDS:
        if age >= lower and (upper is None or age <= upper):
            return band
    return "91+"


def _reflect_association_table(session: Session) -> Table:
    metadata = MetaData()
    return Table(
        "mcp_threat_associations",
        metadata,
        autoload_with=session.get_bind(),
    )


def _association_statement(session: Session, association: Table):
    columns = [association]
    server_id = association.c.get("server_id")
    registry = McpServerRegistry.__table__

    if server_id is not None:
        columns.extend(
            (
                registry.c.name.label("_registry_name"),
                registry.c.risk_tier.label("_registry_risk_tier"),
            )
        )
        statement = select(*columns).select_from(
            association.outerjoin(
                registry,
                cast(server_id, String) == cast(registry.c.server_id, String),
            )
        )
    else:
        statement = select(*columns).select_from(association)
    return statement


def get_threat_association_ageing(
    session: Session = Depends(get_session),
    days: int = 90,
    severity: str | None = None,
) -> dict[str, Any]:
    if not 1 <= days <= 365:
        raise ValueError("days must be between 1 and 365")

    association = _reflect_association_table(session)
    rows = session.execute(_association_statement(session, association)).mappings().all()
    now = datetime.now(timezone.utc)

    counts = {band: 0 for band, _, _ in AGE_BANDS}
    severity_counts: dict[str, int] = {}
    severity_stale_counts: dict[str, int] = {}
    severity_labels: dict[str, str] = {}
    total = 0
    requested_severity = severity.strip().casefold() if severity is not None else None

    for row in rows:
        raw_severity = _extract_value(row, _SEVERITY_KEYS)
        if raw_severity is None:
            severity_label = "unknown"
        else:
            severity_label = str(_scalar(raw_severity)).strip() or "unknown"

        if (
            requested_severity is not None
            and severity_label.casefold() != requested_severity
        ):
            continue

        timestamp_value = None
        for key in _TIMESTAMP_KEYS:
            timestamp_value = _extract_value(row, (key,))
            if timestamp_value is not None:
                break
        timestamp = _parse_datetime(timestamp_value)
        if timestamp is None:
            continue

        exact_age = _age_days(timestamp, now)
        if exact_age > days:
            continue

        age = int(math.floor(exact_age))
        band = _band_for_age(age)
        counts[band] += 1
        total += 1
        severity_counts[severity_label] = severity_counts.get(severity_label, 0) + 1
        if exact_age >= days:
            severity_stale_counts[severity_label] = (
                severity_stale_counts.get(severity_label, 0) + 1
            )
        severity_labels.setdefault(severity_label, severity_label)

    by_severity = [
        {
            "severity": severity_labels[label],
            "count": severity_counts[label],
            "stale_count": severity_stale_counts.get(label, 0),
        }
        for label in sorted(severity_counts, key=str.casefold)
    ]

    return {
        "days": days,
        "bands": [
            {"band": band, "count": counts[band]}
            for band, _, _ in AGE_BANDS
        ],
        "by_severity": by_severity,
        "total": total,
    }


def get_threat_associations_ageing(
    session: Session = Depends(get_session),
    days: int = 90,
    severity: str | None = None,
) -> dict[str, Any]:
    return get_threat_association_ageing(session=session, days=days, severity=severity)


def get_ageing_data(
    session: Session = Depends(get_session),
    days: int = 90,
    severity: str | None = None,
) -> dict[str, Any]:
    return get_threat_association_ageing(session=session, days=days, severity=severity)


def get_threat_association_ageing_data(
    session: Session = Depends(get_session),
    days: int = 90,
    severity: str | None = None,
) -> dict[str, Any]:
    return get_threat_association_ageing(session=session, days=days, severity=severity)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_threat_associations (
                id INTEGER PRIMARY KEY,
                server_id TEXT,
                association_type TEXT,
                severity TEXT,
                payload TEXT,
                created_at DATETIME,
                observed_at DATETIME
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                risk_tier TEXT
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO mcp_server_registry (server_id, name, risk_tier) VALUES (?, ?, ?)",
            ("server-1", "Test Server", "high"),
        )
        now = datetime.now(timezone.utc)
        seeded = (
            ("server-1", "critical", 3),
            ("server-1", "high", 15),
            ("server-1", "medium", 45),
            ("server-1", "low", 120),
        )
        for index, (server_id, row_severity, age) in enumerate(seeded, start=1):
            created = now.replace(microsecond=0) - __import__("datetime").timedelta(
                days=age
            )
            connection.exec_driver_sql(
                """
                INSERT INTO mcp_threat_associations
                    (id, server_id, association_type, severity, payload, created_at, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    index,
                    server_id,
                    "ip",
                    row_severity,
                    json.dumps({"indicator": f"192.0.2.{index}"}),
                    created,
                    created,
                ),
            )

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/threat-associations/ageing")
    def ageing_endpoint(
        days: int = 90,
        severity: str | None = None,
        session: Session = Depends(get_session),
    ):
        return get_threat_association_ageing(
            session=session,
            days=days,
            severity=severity,
        )

    with TestClient(test_app) as client:
        response = client.get(
            "/api/threat-associations/ageing",
            params={"days": 365},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == len(seeded)
        band_counts = {item["band"]: item["count"] for item in payload["bands"]}
        assert band_counts["0-7"] == 1
        assert band_counts["8-30"] == 1
        assert band_counts["31-90"] == 1
        assert band_counts["91+"] == 1

        filtered = client.get(
            "/api/threat-associations/ageing",
            params={"days": 90},
        )
        assert filtered.status_code == 200
        assert filtered.json()["total"] == len(seeded) - 1

        severity_filtered = client.get(
            "/api/threat-associations/ageing",
            params={"days": 365, "severity": "critical"},
        )
        assert severity_filtered.status_code == 200
        assert severity_filtered.json()["total"] == 1

    print("PASS")