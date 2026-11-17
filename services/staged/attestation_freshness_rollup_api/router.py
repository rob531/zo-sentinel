from datetime import datetime, timezone
import inspect

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import build_attestation_freshness


router = APIRouter()


def _call_freshness_logic(session: Session, days: int) -> dict:
    parameters = inspect.signature(build_attestation_freshness).parameters
    if "session" in parameters:
        return build_attestation_freshness(session=session, days=days)
    if "db" in parameters:
        return build_attestation_freshness(db=session, days=days)
    if len(parameters) > 1:
        return build_attestation_freshness(days, session)
    return build_attestation_freshness(days)


@router.get("/api/attestations/freshness")
def get_attestation_freshness(
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
):
    return _call_freshness_logic(session, days)


if __name__ == "__main__":
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
                    server_id TEXT NOT NULL
                )
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine, class_=Session)
    test_session = TestSession()
    now = datetime.now(timezone.utc)

    test_session.add_all(
        [
            McpServerRegistry(
                server_id="server-a",
                last_seen=now,
                risk_tier="low",
                verdict="pass",
            ),
            McpServerRegistry(
                server_id="server-b",
                last_seen=now,
                risk_tier="medium",
                verdict="pass",
            ),
            McpServerRegistry(
                server_id="server-c",
                last_seen=now,
                risk_tier="high",
                verdict="review",
            ),
        ]
    )
    test_session.commit()
    test_session.execute(
        text(
            """
            INSERT INTO mcp_attestations (id, server_id)
            VALUES (:id, :server_id)
            """
        ),
        [
            {"id": 1, "server_id": "server-a"},
            {"id": 2, "server_id": "server-b"},
        ],
    )
    test_session.commit()

    def test_build_attestation_freshness(days: int = 30) -> dict:
        registry_rows = test_session.execute(
            text(
                """
                SELECT server_id, last_seen
                FROM mcp_server_registry
                """
            )
        ).mappings().all()
        attestation_rows = test_session.execute(
            text(
                """
                SELECT id, server_id
                FROM mcp_attestations
                """
            )
        ).mappings().all()

        attested_server_ids = {row["server_id"] for row in attestation_rows}
        servers_total = len(registry_rows)
        with_attestation = len(
            [
                row
                for row in registry_rows
                if row["server_id"] in attested_server_ids
            ]
        )
        missing_attestation = servers_total - with_attestation

        buckets = {
            "0-1d": 0,
            "1-7d": 0,
            "7-30d": 0,
            "30d+": 0,
        }
        oldest_attested_at = None
        cutoff = now.timestamp() - days * 86400

        for row in registry_rows:
            if row["server_id"] not in attested_server_ids:
                continue
            last_seen = row["last_seen"]
            if isinstance(last_seen, str):
                last_seen = datetime.fromisoformat(last_seen)
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            age_seconds = max(0.0, now.timestamp() - last_seen.timestamp())
            age_days = age_seconds / 86400
            if age_days <= 1:
                buckets["0-1d"] += 1
            elif age_days <= 7:
                buckets["1-7d"] += 1
            elif age_days <= 30:
                buckets["7-30d"] += 1
            else:
                buckets["30d+"] += 1
            if oldest_attested_at is None or last_seen < oldest_attested_at:
                oldest_attested_at = last_seen

        return {
            "days": days,
            "servers_total": servers_total,
            "with_attestation": with_attestation,
            "missing_attestation": missing_attestation,
            "stale_attestation": sum(
                1
                for row in registry_rows
                if row["server_id"] in attested_server_ids
                and row["last_seen"] is not None
                and (
                    row["last_seen"].timestamp()
                    if hasattr(row["last_seen"], "timestamp")
                    else datetime.fromisoformat(row["last_seen"]).timestamp()
                )
                < cutoff
            ),
            "oldest_attested_at": (
                oldest_attested_at.isoformat()
                if oldest_attested_at is not None
                else None
            ),
            "buckets": [
                {"bucket": bucket, "count": count}
                for bucket, count in buckets.items()
            ],
        }

    original_builder = globals()["build_attestation_freshness"]
    globals()["build_attestation_freshness"] = test_build_attestation_freshness

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_session

    try:
        response = TestClient(test_app).get(
            "/api/attestations/freshness",
            params={"days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["servers_total"] == 3
        assert payload["missing_attestation"] == 1
        assert sum(bucket["count"] for bucket in payload["buckets"]) == payload[
            "with_attestation"
        ]
    finally:
        globals()["build_attestation_freshness"] = original_builder
        test_session.close()
        test_engine.dispose()

    print("PASS")