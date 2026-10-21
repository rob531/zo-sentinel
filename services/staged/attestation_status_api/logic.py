from __future__ import annotations

"""Attestation status API logic.

The live attestation table is read through the application's real SQLAlchemy
session.  The attestation table is not represented by an app model, so its
columns are discovered from the live schema and only existing columns are
selected.  Column mapping is:

* ``status``: ``status``, ``attestation_status``, ``verification_status``, or
  ``state``; when none exists, ``attestation_type`` is used as the status.
* ``issued_at``: ``issued_at``, ``attested_at``, ``created_at``, or ``issued``.
* ``expires_at``: ``expires_at``, ``expiry``, ``valid_until``, or ``expires``.

A row with no expiry is considered current unless its status explicitly says
that it is stale, expired, revoked, or invalid.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


class AttestationDetails(BaseModel):
    status: Optional[str] = None
    issued_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    stale: bool = False


class AttestationStatusResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    attestation: Optional[AttestationDetails] = None


class FleetSummaryResponse(BaseModel):
    total_servers: int
    with_attestation: int
    without_attestation: int
    stale_count: int
    fleet: Dict[str, int]


_ATTESTATION_TABLE = "mcp_attestations"
_STATUS_COLUMNS = (
    "status",
    "attestation_status",
    "verification_status",
    "state",
)
_ISSUED_COLUMNS = (
    "issued_at",
    "attested_at",
    "created_at",
    "issued",
)
_EXPIRY_COLUMNS = (
    "expires_at",
    "expiry",
    "valid_until",
    "expires",
)


def _table_columns(session: Session, table_name: str) -> Dict[str, Any]:
    """Return the live columns for a table, or an empty mapping if absent."""

    try:
        inspector = inspect(session.get_bind())
        return {column["name"]: column for column in inspector.get_columns(table_name)}
    except Exception:
        return {}


def _first_present(columns: Dict[str, Any], candidates: Iterable[str]) -> Optional[str]:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text_value = str(value).strip()
    if not text_value:
        return None
    if text_value.endswith("Z"):
        text_value = text_value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text_value)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text_value, fmt)
            except ValueError:
                continue
    return None


def _is_expired(value: Any, now: datetime) -> bool:
    parsed = _parse_datetime(value)
    if parsed is None:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed < now


def _status_is_stale(status: Any) -> bool:
    if status is None:
        return False
    normalized = str(status).strip().lower()
    return any(
        marker in normalized
        for marker in ("stale", "expired", "revoked", "invalid")
    )


def _fetch_attestation_rows(session: Session) -> List[Dict[str, Any]]:
    columns = _table_columns(session, _ATTESTATION_TABLE)
    if not columns or "server_id" not in columns:
        return []

    selected = ["server_id"]
    for optional in (
        "id",
        "attestation_type",
        _first_present(columns, _STATUS_COLUMNS),
        _first_present(columns, _ISSUED_COLUMNS),
        _first_present(columns, _EXPIRY_COLUMNS),
    ):
        if optional and optional not in selected:
            selected.append(optional)

    # Column names come from the inspected live schema, never from request
    # input.  Values are still bound as query parameters.
    statement = text(
        "SELECT {columns} FROM {table}".format(
            columns=", ".join(selected),
            table=_ATTESTATION_TABLE,
        )
    )
    result = session.execute(statement)
    return [dict(row._mapping) for row in result]


def _latest_attestation(
    rows: Iterable[Dict[str, Any]], server_id: str
) -> Optional[Dict[str, Any]]:
    matching = [row for row in rows if str(row.get("server_id")) == str(server_id)]
    if not matching:
        return None

    issued_column = _first_present(
        {key: None for key in matching[0]}, _ISSUED_COLUMNS
    )
    if issued_column is None:
        for candidate in _ISSUED_COLUMNS:
            if candidate in matching[0]:
                issued_column = candidate
                break

    def sort_key(row: Dict[str, Any]) -> tuple:
        issued = _parse_datetime(row.get(issued_column)) if issued_column else None
        if issued is None:
            issued = datetime.min.replace(tzinfo=timezone.utc)
        elif issued.tzinfo is None:
            issued = issued.replace(tzinfo=timezone.utc)
        row_id = row.get("id")
        try:
            numeric_id = int(row_id)
        except (TypeError, ValueError):
            numeric_id = 0
        return issued, numeric_id

    return max(matching, key=sort_key)


def _attestation_details(
    row: Optional[Dict[str, Any]], now: Optional[datetime] = None
) -> Optional[AttestationDetails]:
    if row is None:
        return None

    now = now or datetime.now(timezone.utc)
    status_column = _first_present(
        {key: None for key in row}, _STATUS_COLUMNS
    )
    if status_column is None:
        status_column = "attestation_type" if "attestation_type" in row else None

    issued_column = _first_present({key: None for key in row}, _ISSUED_COLUMNS)
    if issued_column is None:
        issued_column = "issued_at" if "issued_at" in row else None

    expiry_column = _first_present({key: None for key in row}, _EXPIRY_COLUMNS)
    if expiry_column is None:
        expiry_column = "expires_at" if "expires_at" in row else None

    status = row.get(status_column) if status_column else None
    issued_at = row.get(issued_column) if issued_column else None
    expires_at = row.get(expiry_column) if expiry_column else None

    stale = _status_is_stale(status) or _is_expired(expires_at, now)
    return AttestationDetails(
        status=str(status) if status is not None else None,
        issued_at=_parse_datetime(issued_at),
        expires_at=_parse_datetime(expires_at),
        stale=stale,
    )


def _registry_servers(session: Session, server_id: Optional[str] = None) -> List[McpServerRegistry]:
    statement = select(McpServerRegistry)
    if server_id is not None:
        statement = statement.where(McpServerRegistry.server_id == server_id)
    return list(session.execute(statement).scalars().all())


def get_attestation_status(
    server_id: str, session: Session = Depends(get_session)
) -> AttestationStatusResponse:
    servers = _registry_servers(session, server_id)
    if not servers:
        raise HTTPException(status_code=404, detail="Server not found")

    server = servers[0]
    rows = _fetch_attestation_rows(session)
    latest = _latest_attestation(rows, server_id)

    return AttestationStatusResponse(
        server_id=str(server.server_id),
        name=server.name,
        risk_tier=server.risk_tier,
        verdict=server.verdict,
        attestation=_attestation_details(latest),
    )


def get_fleet_summary(
    session: Session = Depends(get_session),
) -> FleetSummaryResponse:
    servers = _registry_servers(session)
    rows = _fetch_attestation_rows(session)
    now = datetime.now(timezone.utc)

    with_attestation = 0
    stale_count = 0
    for server in servers:
        latest = _latest_attestation(rows, str(server.server_id))
        if latest is not None:
            with_attestation += 1
            details = _attestation_details(latest, now)
            if details is not None and details.stale:
                stale_count += 1

    total_servers = len(servers)
    summary = {
        "total_servers": total_servers,
        "with_attestation": with_attestation,
        "without_attestation": total_servers - with_attestation,
        "stale_count": stale_count,
    }
    return FleetSummaryResponse(**summary, fleet=summary)


@router.get("/attestation/status", response_model=AttestationStatusResponse)
def attestation_status(
    server_id: str, session: Session = Depends(get_session)
) -> AttestationStatusResponse:
    return get_attestation_status(server_id, session)


@router.get("/attestation/fleet-summary", response_model=FleetSummaryResponse)
def attestation_fleet_summary(
    session: Session = Depends(get_session),
) -> FleetSummaryResponse:
    return get_fleet_summary(session)


__all__ = [
    "router",
    "AttestationDetails",
    "AttestationStatusResponse",
    "FleetSummaryResponse",
    "get_attestation_status",
    "get_fleet_summary",
]


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_attestations (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR NOT NULL,
                    attestation_type VARCHAR,
                    status VARCHAR,
                    issued_at DATETIME,
                    expires_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, risk_tier, verdict)
                VALUES
                    ('fresh-server', 'Fresh Server', 'low', 'trusted'),
                    ('unattested-server', 'Unattested Server', 'medium', 'unknown'),
                    ('stale-server', 'Stale Server', 'high', 'review')
                """
            )
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO mcp_attestations
                    (server_id, attestation_type, status, issued_at, expires_at)
                VALUES
                    ('fresh-server', 'non_binding', 'valid', :issued, :expires),
                    ('stale-server', 'non_binding', 'valid', :issued, :stale_expires)
                """
            ),
            {
                "issued": now - timedelta(days=1),
                "expires": now + timedelta(days=30),
                "stale_expires": now - timedelta(days=1),
            },
        )

    TestSession = sessionmaker(bind=test_engine)
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: TestSession()

    with TestClient(test_app) as client:
        fresh_response = client.get(
            "/api/attestation/status", params={"server_id": "fresh-server"}
        )
        assert fresh_response.status_code == 200
        fresh = fresh_response.json()
        assert fresh["name"] == "Fresh Server"
        assert fresh["attestation"]["stale"] is False

        fleet_response = client.get("/api/attestation/fleet-summary")
        assert fleet_response.status_code == 200
        fleet = fleet_response.json()
        assert fleet["fleet"]["total_servers"] == 3
        assert fleet["total_servers"] == 3

    print("PASS")