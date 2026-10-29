import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api/attestations", tags=["attestations"])


class StateCounts(BaseModel):
    valid: int
    expired: int
    revoked: int
    pending: int


class RuleVersionCount(BaseModel):
    decision_rule_version: Optional[str]
    count: int


class CoverageProfileResponse(BaseModel):
    total: int
    by_state: StateCounts
    by_rule_version: List[RuleVersionCount]
    coverage_pct: float
    oldest_valid_from: Optional[str]
    newest_expires_at: Optional[str]


def _write_service_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        raw_rows = payload
    elif isinstance(payload, dict):
        raw_rows = None
        for key in ("rows", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                raw_rows = value
                break
        if raw_rows is None and isinstance(payload.get("result"), dict):
            result = payload["result"]
            for key in ("rows", "data"):
                value = result.get(key)
                if isinstance(value, list):
                    raw_rows = value
                    break
        if raw_rows is None and isinstance(payload.get("result"), list):
            raw_rows = payload["result"]
        if raw_rows is None:
            raw_rows = []
    else:
        raw_rows = []

    rows: List[Dict[str, Any]] = []
    columns = (
        "server_id",
        "attestation_hash",
        "valid_from",
        "expires_at",
        "revoked",
        "decision_rule_version",
        "created_at",
    )
    for raw_row in raw_rows:
        if isinstance(raw_row, dict):
            rows.append(dict(raw_row))
        elif isinstance(raw_row, (list, tuple)):
            rows.append(dict(zip(columns, raw_row)))
    return rows


def _fetch_bus_attestations() -> List[Dict[str, Any]]:
    sql = (
        "SELECT server_id, attestation_hash, valid_from, expires_at, revoked, "
        "decision_rule_version, created_at FROM mcp_attestations"
    )
    request = Request(
        "http://127.0.0.1:8772/query",
        data=json.dumps({"sql": sql, "params": []}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return _write_service_rows(payload)


def _fetch_sqlite_attestations(session: Session) -> List[Dict[str, Any]]:
    sql = (
        "SELECT a.server_id, a.attestation_hash, a.valid_from, a.expires_at, "
        "a.revoked, a.decision_rule_version, a.created_at "
        "FROM mcp_attestations AS a "
        "INNER JOIN mcp_server_registry AS r ON r.server_id = a.server_id"
    )
    result = session.execute(text(sql))
    return [dict(row) for row in result.mappings().all()]


def get_attestation_rows(
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    try:
        bind = session.get_bind()
        if (
            bind is not None
            and bind.dialect.name == "sqlite"
            and inspect(bind).has_table("mcp_attestations")
        ):
            return _fetch_sqlite_attestations(session)
    except Exception:
        pass
    return _fetch_bus_attestations()


def _as_utc_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 100_000_000_000:
            seconds /= 1000.0
        try:
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_revoked(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y", "revoked"}
    return bool(value)


def _isoformat(value: Any) -> Optional[str]:
    parsed = _as_utc_datetime(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


@router.get("/coverage-profile", response_model=CoverageProfileResponse)
def get_coverage_profile(
    session: Session = Depends(get_session),
    attestations: List[Dict[str, Any]] = Depends(get_attestation_rows),
) -> CoverageProfileResponse:
    registry_result = session.execute(
        text("SELECT DISTINCT server_id FROM mcp_server_registry")
    )
    registry_ids = {
        str(row[0])
        for row in registry_result
        if row[0] is not None
    }

    relevant = [
        row
        for row in attestations
        if row.get("server_id") is not None
        and str(row.get("server_id")) in registry_ids
    ]

    now = datetime.now(timezone.utc)
    state_counts = {"valid": 0, "expired": 0, "revoked": 0, "pending": 0}
    rule_counts: Dict[Optional[str], int] = {}
    covered_servers = set()
    valid_from_values = []
    expires_at_values = []

    for row in relevant:
        revoked = _is_revoked(row.get("revoked"))
        valid_from = _as_utc_datetime(row.get("valid_from"))
        expires_at = _as_utc_datetime(row.get("expires_at"))

        if revoked:
            state = "revoked"
        elif expires_at is not None and now >= expires_at:
            state = "expired"
        elif valid_from is not None and now < valid_from:
            state = "pending"
        else:
            state = "valid"

        state_counts[state] += 1

        version = row.get("decision_rule_version")
        version_key = str(version) if version is not None else None
        rule_counts[version_key] = rule_counts.get(version_key, 0) + 1

        server_id = str(row.get("server_id"))
        if not revoked:
            covered_servers.add(server_id)

        if valid_from is not None:
            valid_from_values.append(valid_from)
        if expires_at is not None:
            expires_at_values.append(expires_at)

    coverage_pct = (
        round((len(covered_servers) / len(registry_ids)) * 100.0, 2)
        if registry_ids
        else 0.0
    )

    oldest_valid_from = (
        min(valid_from_values).isoformat().replace("+00:00", "Z")
        if valid_from_values
        else None
    )
    newest_expires_at = (
        max(expires_at_values).isoformat().replace("+00:00", "Z")
        if expires_at_values
        else None
    )

    by_rule_version = [
        RuleVersionCount(decision_rule_version=version, count=count)
        for version, count in sorted(
            rule_counts.items(),
            key=lambda item: (item[0] is None, item[0] or ""),
        )
    ]

    return CoverageProfileResponse(
        total=len(relevant),
        by_state=StateCounts(**state_counts),
        by_rule_version=by_rule_version,
        coverage_pct=coverage_pct,
        oldest_valid_from=oldest_valid_from,
        newest_expires_at=newest_expires_at,
    )


get_attestation_coverage_profile = get_coverage_profile


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestingSessionLocal = sessionmaker(
        bind=test_engine,
        autocommit=False,
        autoflush=False,
    )

    test_db = TestingSessionLocal()
    test_db.execute(
        text(
            "CREATE TABLE mcp_attestations ("
            "server_id TEXT PRIMARY KEY, "
            "attestation_hash TEXT NOT NULL, "
            "valid_from TEXT NOT NULL, "
            "expires_at TEXT NOT NULL, "
            "revoked BOOLEAN NOT NULL, "
            "decision_rule_version TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
    )

    for index in range(1, 4):
        test_db.add(
            McpServerRegistry(
                server_id=f"server-{index}",
                name=f"Server {index}",
                risk_tier="medium",
                verdict="approved",
            )
        )
    test_db.flush()

    current_time = datetime.now(timezone.utc)
    test_rows = [
        (
            "server-1",
            "hash-1",
            (current_time - timedelta(hours=1)).isoformat(),
            (current_time + timedelta(hours=1)).isoformat(),
            0,
            "v1",
            current_time.isoformat(),
        ),
        (
            "server-2",
            "hash-2",
            (current_time - timedelta(hours=2)).isoformat(),
            (current_time - timedelta(hours=1)).isoformat(),
            0,
            "v1",
            current_time.isoformat(),
        ),
        (
            "server-3",
            "hash-3",
            (current_time - timedelta(hours=1)).isoformat(),
            (current_time + timedelta(hours=1)).isoformat(),
            1,
            "v2",
            current_time.isoformat(),
        ),
    ]
    test_db.execute(
        text(
            "INSERT INTO mcp_attestations ("
            "server_id, attestation_hash, valid_from, expires_at, revoked, "
            "decision_rule_version, created_at) "
            "VALUES (:server_id, :attestation_hash, :valid_from, :expires_at, "
            ":revoked, :decision_rule_version, :created_at)"
        ),
        [
            {
                "server_id": row[0],
                "attestation_hash": row[1],
                "valid_from": row[2],
                "expires_at": row[3],
                "revoked": row[4],
                "decision_rule_version": row[5],
                "created_at": row[6],
            }
            for row in test_rows
        ],
    )
    test_db.commit()

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/attestations/coverage-profile")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert sum(payload["by_state"].values()) == 3
        assert 0 <= payload["coverage_pct"] <= 100
        assert "by_rule_version" in payload

    print("PASS")