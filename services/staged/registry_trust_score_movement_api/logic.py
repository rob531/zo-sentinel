from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any) -> str | None:
    parsed = _as_utc(value)
    return parsed.isoformat() if parsed is not None else None


def _bucket_start(value: datetime, bucket: str) -> datetime:
    value = value.astimezone(timezone.utc)
    if bucket == "week":
        return (value - timedelta(days=value.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _bucketed_average(rows: list[McpServerRegistry], bucket: str) -> float | None:
    buckets: dict[datetime, list[float]] = defaultdict(list)
    for row in rows:
        assessed = _as_utc(row.last_assessed)
        trust = row.trust_score
        if assessed is None or trust is None:
            continue
        buckets[_bucket_start(assessed, bucket)].append(float(trust))
    if not buckets:
        return None
    bucket_means = [sum(values) / len(values) for values in buckets.values() if values]
    return sum(bucket_means) / len(bucket_means) if bucket_means else None


def _tier_moves(
    rows: list[McpServerRegistry], bucket: str
) -> list[dict[str, Any]]:
    by_server: dict[str, list[McpServerRegistry]] = defaultdict(list)
    for row in rows:
        if row.server_id is not None:
            by_server[str(row.server_id)].append(row)

    candidates: list[tuple[list[McpServerRegistry], str]] = []
    for server_rows in by_server.values():
        if len(server_rows) > 1:
            candidates.append((server_rows, "server_id"))

    used_server_ids: set[str] = set()
    for server_rows, _ in candidates:
        used_server_ids.update(str(row.server_id) for row in server_rows)

    by_name: dict[tuple[str, str], list[McpServerRegistry]] = defaultdict(list)
    for row in rows:
        if row.server_id is None or str(row.server_id) in used_server_ids:
            continue
        if row.name and row.registry_source:
            by_name[(str(row.registry_source), str(row.name))].append(row)

    for name_rows in by_name.values():
        if len({str(row.server_id) for row in name_rows}) > 1:
            candidates.append((name_rows, "name"))

    moves: list[dict[str, Any]] = []
    seen_groups: set[tuple[str, str]] = set()

    for group_rows, identity_kind in candidates:
        ordered = sorted(
            group_rows,
            key=lambda row: (
                _as_utc(row.last_assessed) or datetime.min.replace(tzinfo=timezone.utc),
                str(row.server_id),
            ),
        )
        old_row = ordered[0]
        new_row = ordered[-1]
        old_tier = old_row.risk_tier
        new_tier = new_row.risk_tier
        if old_tier == new_tier:
            continue

        if identity_kind == "server_id":
            group_key = ("server", str(new_row.server_id))
        else:
            group_key = (
                "name",
                f"{new_row.registry_source}:{new_row.name}",
            )
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)

        old_trust = (
            float(old_row.trust_score) if old_row.trust_score is not None else None
        )
        new_trust = (
            float(new_row.trust_score) if new_row.trust_score is not None else None
        )
        delta = (
            new_trust - old_trust
            if old_trust is not None and new_trust is not None
            else None
        )

        moves.append(
            {
                "server_id": new_row.server_id,
                "name": new_row.name,
                "old_tier": old_tier,
                "new_tier": new_tier,
                "old_trust": old_trust,
                "new_trust": new_trust,
                "delta": delta,
                "last_assessed": _iso(new_row.last_assessed),
            }
        )

    moves.sort(
        key=lambda move: (
            str(move.get("registry_source") or ""),
            str(move.get("server_id") or ""),
        )
    )
    return moves


def get_registry_trust_score_movement(
    days: int = 30,
    bucket: str = "day",
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    if days <= 0:
        raise ValueError("days must be greater than zero")
    if bucket not in {"day", "week"}:
        raise ValueError("bucket must be 'day' or 'week'")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    previous_cutoff = cutoff - timedelta(days=days)

    current_rows = list(
        db.execute(
            select(McpServerRegistry).where(
                McpServerRegistry.last_assessed.is_not(None),
                McpServerRegistry.last_assessed >= cutoff,
                McpServerRegistry.last_assessed <= now,
            )
        ).scalars()
    )
    previous_rows = list(
        db.execute(
            select(McpServerRegistry).where(
                McpServerRegistry.last_assessed.is_not(None),
                McpServerRegistry.last_assessed >= previous_cutoff,
                McpServerRegistry.last_assessed < cutoff,
            )
        ).scalars()
    )

    rows_by_source: dict[str, list[McpServerRegistry]] = defaultdict(list)
    previous_by_source: dict[str, list[McpServerRegistry]] = defaultdict(list)
    for row in current_rows:
        if row.registry_source:
            rows_by_source[str(row.registry_source)].append(row)
    for row in previous_rows:
        if row.registry_source:
            previous_by_source[str(row.registry_source)].append(row)

    sources: list[dict[str, Any]] = []
    for registry_source in sorted(rows_by_source):
        source_rows = rows_by_source[registry_source]
        tier_counts: dict[str, int] = defaultdict(int)
        for row in source_rows:
            if row.risk_tier is not None:
                tier_counts[str(row.risk_tier)] += 1

        avg_trust = _bucketed_average(source_rows, bucket)
        prev_avg = _bucketed_average(previous_by_source.get(registry_source, []), bucket)
        delta_avg = (
            avg_trust - prev_avg
            if avg_trust is not None and prev_avg is not None
            else None
        )
        server_ids = {
            str(row.server_id) for row in source_rows if row.server_id is not None
        }
        moves = _tier_moves(source_rows, bucket)
        for move in moves:
            move["registry_source"] = registry_source

        sources.append(
            {
                "registry_source": registry_source,
                "servers": len(server_ids),
                "assessed": len(source_rows),
                "avg_trust_score": avg_trust,
                "prev_avg_trust_score": prev_avg,
                "delta_avg": delta_avg,
                "tier_counts": dict(sorted(tier_counts.items())),
                "tier_moves": moves,
            }
        )

    return {
        "days": days,
        "bucket": bucket,
        "generated_at": now.isoformat(),
        "sources": sources,
    }


def get_trust_score_movement(
    days: int = 30,
    bucket: str = "day",
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_registry_trust_score_movement(days=days, bucket=bucket, db=db)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="registry-alpha-old",
                    name="alpha",
                    registry_source="pypi",
                    trust_score=40,
                    risk_tier="HIGH",
                    last_assessed=now - timedelta(days=2),
                ),
                McpServerRegistry(
                    server_id="registry-alpha-new",
                    name="alpha",
                    registry_source="pypi",
                    trust_score=70,
                    risk_tier="MEDIUM",
                    last_assessed=now - timedelta(hours=1),
                ),
                McpServerRegistry(
                    server_id="registry-beta",
                    name="beta",
                    registry_source="npm",
                    trust_score=80,
                    risk_tier="LOW",
                    last_assessed=now - timedelta(hours=3),
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/registry/trust-score-movement")
    def trust_score_movement(
        days: int = 30,
        bucket: str = "day",
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_registry_trust_score_movement(
            days=days, bucket=bucket, db=db
        )

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get(
        "/api/registry/trust-score-movement",
        params={"days": 30, "bucket": "day"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["days"] == 30
    assert payload["bucket"] == "day"
    assert len(payload["sources"]) == 2
    assert sum(payload["sources"][0]["tier_counts"].values()) == payload["sources"][0][
        "assessed"
    ]
    assert any(
        move["old_tier"] is not None and move["new_tier"] is not None
        for source in payload["sources"]
        for move in source["tier_moves"]
    )
    print("PASS")