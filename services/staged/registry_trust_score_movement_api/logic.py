from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

__all__ = [
    "get_trust_score_movement",
    "get_trust_score_movement_report",
    "build_trust_score_movement",
]


def _validated_days(days: int) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = 30
    return max(1, min(value, 3650))


def _validated_bucket(bucket: str) -> str:
    value = (bucket or "day").strip().lower()
    if value not in {"day", "week"}:
        raise ValueError("bucket must be either 'day' or 'week'")
    return value


def get_trust_score_movement(
    session: Session,
    days: int = 30,
    bucket: str = "day",
) -> dict[str, Any]:
    """Compute trust-score and risk-tier movement for registry servers."""

    window_days = _validated_days(days)
    bucket_name = _validated_bucket(bucket)
    cutoff = datetime.utcnow() - timedelta(days=window_days)

    query = text(
        """
        WITH ranked AS (
            SELECT
                r.server_id,
                r.name,
                r.registry_source,
                r.trust_score,
                r.risk_tier,
                r.last_assessed,
                ROW_NUMBER() OVER (
                    PARTITION BY r.server_id
                    ORDER BY r.last_assessed DESC, r.last_seen DESC, r.first_seen DESC
                ) AS current_rank,
                ROW_NUMBER() OVER (
                    PARTITION BY r.server_id
                    ORDER BY r.last_assessed ASC, r.last_seen ASC, r.first_seen ASC
                ) AS oldest_rank
            FROM mcp_server_registry AS r
            WHERE r.last_assessed IS NOT NULL
              AND r.last_assessed >= :cutoff
        )
        SELECT
            current.server_id,
            current.name,
            current.registry_source,
            current.trust_score,
            current.risk_tier,
            current.last_assessed,
            previous.risk_tier AS old_tier,
            previous.trust_score AS old_trust,
            previous.last_assessed AS old_last_assessed
        FROM ranked AS current
        INNER JOIN ranked AS previous
            ON previous.server_id = current.server_id
           AND previous.oldest_rank = 1
        WHERE current.current_rank = 1
        ORDER BY current.registry_source, current.server_id
        """
    )

    rows = session.execute(query, {"cutoff": cutoff}).mappings().all()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        source = row["registry_source"] or "unknown"
        grouped.setdefault(str(source), []).append(dict(row))

    sources: list[dict[str, Any]] = []
    for source in sorted(grouped):
        source_rows = grouped[source]
        current_trust = [
            float(row["trust_score"])
            for row in source_rows
            if row["trust_score"] is not None
        ]
        previous_trust = [
            float(row["old_trust"])
            for row in source_rows
            if row["old_trust"] is not None
        ]

        tier_counts: dict[str, int] = {}
        for row in source_rows:
            tier = str(row["risk_tier"] or "UNKNOWN").upper()
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

        tier_moves: list[dict[str, Any]] = []
        for row in source_rows:
            old_tier = row["old_tier"]
            new_tier = row["risk_tier"]
            if old_tier is None or new_tier is None:
                continue
            if str(old_tier).upper() == str(new_tier).upper():
                continue

            old_trust = row["old_trust"]
            new_trust = row["trust_score"]
            delta = None
            if old_trust is not None and new_trust is not None:
                delta = float(new_trust) - float(old_trust)

            tier_moves.append(
                {
                    "server_id": row["server_id"],
                    "name": row["name"],
                    "old_tier": old_tier,
                    "new_tier": new_tier,
                    "old_trust": old_trust,
                    "new_trust": new_trust,
                    "delta": delta,
                    "last_assessed": row["last_assessed"],
                }
            )

        avg_trust = (
            sum(current_trust) / len(current_trust)
            if current_trust
            else None
        )
        prev_avg_trust = (
            sum(previous_trust) / len(previous_trust)
            if previous_trust
            else None
        )
        delta_avg = (
            avg_trust - prev_avg_trust
            if avg_trust is not None and prev_avg_trust is not None
            else None
        )

        sources.append(
            {
                "registry_source": source,
                "servers": len(source_rows),
                "assessed": len(source_rows),
                "avg_trust_score": avg_trust,
                "prev_avg_trust_score": prev_avg_trust,
                "delta_avg": delta_avg,
                "tier_counts": tier_counts,
                "tier_moves": tier_moves,
            }
        )

    return {
        "days": window_days,
        "bucket": bucket_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
    }


get_trust_score_movement_report = get_trust_score_movement
build_trust_score_movement = get_trust_score_movement


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT,
                    name TEXT,
                    registry_source TEXT,
                    trust_score REAL,
                    verdict TEXT,
                    risk_tier TEXT,
                    last_assessed TIMESTAMP,
                    first_seen TIMESTAMP,
                    last_seen TIMESTAMP,
                    last_scanned TIMESTAMP,
                    scan_count INTEGER DEFAULT 0,
                    meta TEXT
                )
                """
            )
        )
        now = datetime.utcnow()
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, trust_score, verdict,
                     risk_tier, last_assessed, first_seen, last_seen, scan_count, meta)
                VALUES
                    (:server_id, :name, :registry_source, :trust_score, :verdict,
                     :risk_tier, :last_assessed, :first_seen, :last_seen, :scan_count, :meta)
                """
            ),
            [
                {
                    "server_id": "server-a",
                    "name": "Server A",
                    "registry_source": "registry-one",
                    "trust_score": 0.40,
                    "verdict": "review",
                    "risk_tier": "HIGH",
                    "last_assessed": now - timedelta(days=3),
                    "first_seen": now - timedelta(days=10),
                    "last_seen": now,
                    "scan_count": 1,
                    "meta": "{}",
                },
                {
                    "server_id": "server-a",
                    "name": "Server A",
                    "registry_source": "registry-one",
                    "trust_score": 0.80,
                    "verdict": "trusted",
                    "risk_tier": "LOW",
                    "last_assessed": now - timedelta(days=1),
                    "first_seen": now - timedelta(days=10),
                    "last_seen": now,
                    "scan_count": 2,
                    "meta": "{}",
                },
                {
                    "server_id": "server-b",
                    "name": "Server B",
                    "registry_source": "registry-two",
                    "trust_score": 0.60,
                    "verdict": "trusted",
                    "risk_tier": "MEDIUM",
                    "last_assessed": now - timedelta(days=2),
                    "first_seen": now - timedelta(days=8),
                    "last_seen": now,
                    "scan_count": 1,
                    "meta": "{}",
                },
            ],
        )

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()

    @that_app.get("/api/registry/trust-score-movement")
    def trust_score_movement(
        days: int = 30,
        bucket: str = "day",
        session: Session = Depends(get_session),
    ):
        return get_trust_score_movement(session, days=days, bucket=bucket)

    that_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(that_app).get(
        "/api/registry/trust-score-movement",
        params={"days": 30, "bucket": "day"},
    )
    assert response.status_code == 200
    payload = response.json()
    for source in payload["sources"]:
        assert sum(source["tier_counts"].values()) == source["assessed"]
    moves = [
        move
        for source in payload["sources"]
        for move in source["tier_moves"]
    ]
    assert moves
    assert moves[0]["old_tier"] is not None
    assert moves[0]["new_tier"] is not None
    print("PASS")


if __name__ == "__main__":
    _run_self_test()