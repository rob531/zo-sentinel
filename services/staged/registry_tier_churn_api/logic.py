from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _as_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _date_value(value: Any) -> date | None:
    parsed = _as_utc_datetime(value)
    return parsed.date() if parsed is not None else None


def _clean_tier(value: Any) -> str | None:
    if value is None:
        return None
    tier = str(value).strip()
    return tier or None


def _registry_rows(session: Session) -> list[dict[str, Any]]:
    result = session.execute(
        text(
            """
            SELECT
                server_id,
                risk_tier,
                last_assessed
            FROM mcp_server_registry
            """
        )
    )
    return [dict(row._mapping) for row in result.all()]


def _overall_axis_rows(session: Session) -> list[dict[str, Any]]:
    result = session.execute(
        text(
            """
            SELECT
                id,
                server_id,
                label,
                scored_at
            FROM mcp_llm_axis_scores
            WHERE axis_name = :axis_name
            ORDER BY scored_at ASC, id ASC
            """
        ),
        {"axis_name": "overall_risk"},
    )
    return [dict(row._mapping) for row in result.all()]


def compute_tier_churn(session: Session, days: int = 7) -> dict[str, Any]:
    if days < 1:
        days = 1

    now = datetime.now(timezone.utc)
    start_date = (now - timedelta(days=days - 1)).date()
    registry = _registry_rows(session)
    axis_rows = _overall_axis_rows(session)

    axis_by_server: dict[Any, list[dict[str, Any]]] = {}
    for row in axis_rows:
        axis_by_server.setdefault(row.get("server_id"), []).append(row)

    server_tier: dict[Any, str] = {}
    registry_date: dict[Any, date] = {}
    registry_events: dict[Any, list[tuple[date, str]]] = {}

    for row in registry:
        server_id = row.get("server_id")
        tier = _clean_tier(row.get("risk_tier"))

        if tier is None:
            candidates = axis_by_server.get(server_id, [])
            for candidate in reversed(candidates):
                candidate_tier = _clean_tier(candidate.get("label"))
                if candidate_tier is not None:
                    tier = candidate_tier
                    break

        if tier is None:
            tier = "UNKNOWN"
        server_tier[server_id] = tier

        assessed_date = _date_value(row.get("last_assessed"))
        if assessed_date is not None:
            registry_date[server_id] = assessed_date
            registry_events.setdefault(server_id, []).append((assessed_date, tier))

    tier_counts: dict[str, int] = {}
    for tier in server_tier.values():
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    all_dates = sorted(
        {
            event_date
            for events in registry_events.values()
            for event_date, _ in events
            if start_date <= event_date <= now.date()
        }
    )

    events_by_date: dict[date, list[tuple[Any, str]]] = {day: [] for day in all_dates}
    for server_id, events in registry_events.items():
        for event_date, tier in events:
            if event_date in events_by_date:
                events_by_date[event_date].append((server_id, tier))

    previous_tier: dict[Any, str] = {}
    churn: list[dict[str, Any]] = []

    for event_date in all_dates:
        current_events = events_by_date[event_date]
        current_tier = dict(previous_tier)
        current_tier.update({server_id: tier for server_id, tier in current_events})

        entered: dict[str, int] = {}
        exited: dict[str, int] = {}
        changed_servers = 0

        for server_id, tier in current_events:
            old_tier = previous_tier.get(server_id)
            if old_tier is None:
                entered[tier] = entered.get(tier, 0) + 1
                changed_servers += 1
            elif old_tier != tier:
                entered[tier] = entered.get(tier, 0) + 1
                exited[old_tier] = exited.get(old_tier, 0) + 1
                changed_servers += 1

        churn.append(
            {
                "date": event_date.isoformat(),
                "changed_servers": changed_servers,
                "entered": entered,
                "exited": exited,
            }
        )
        previous_tier = current_tier

    total_changed = sum(item["changed_servers"] for item in churn)
    server_count = len(server_tier)
    churn_rate = float(total_changed / server_count) if server_count else 0.0

    from_value = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    return {
        "days": days,
        "from": from_value.isoformat(),
        "to": now.isoformat(),
        "tier_counts": tier_counts,
        "churn": churn,
        "churn_rate": churn_rate,
    }


def get_tier_churn(
    days: int = Query(default=7, ge=1, le=365),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return compute_tier_churn(session=session, days=days)


def get_registry_tier_churn(
    days: int = Query(default=7, ge=1, le=365),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return compute_tier_churn(session=session, days=days)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
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
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    registry_source TEXT,
                    url TEXT,
                    description TEXT,
                    trust_score REAL,
                    verdict TEXT,
                    verdict_reasoning TEXT,
                    confidence REAL,
                    last_assessed TEXT,
                    first_seen TEXT,
                    last_seen TEXT,
                    last_scanned TEXT,
                    scan_count INTEGER,
                    risk_tier TEXT,
                    meta TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    axis_name TEXT,
                    label TEXT,
                    label_index INTEGER,
                    probs TEXT,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
                    escalated INTEGER,
                    escalated_to TEXT,
                    decision_rule_version TEXT,
                    model_version TEXT,
                    adapter_sha256 TEXT,
                    scored_at TEXT
                )
                """
            )
        )

        today = datetime.now(timezone.utc).date()
        dates = [today - timedelta(days=1), today]
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, url, description,
                     trust_score, verdict, verdict_reasoning, confidence,
                     last_assessed, first_seen, last_seen, last_scanned,
                     scan_count, risk_tier, meta)
                VALUES
                    (:server_id, :name, :registry_source, :url, :description,
                     :trust_score, :verdict, :verdict_reasoning, :confidence,
                     :last_assessed, :first_seen, :last_seen, :last_scanned,
                     :scan_count, :risk_tier, :meta),
                    (:server_id_2, :name_2, :registry_source_2, :url_2, :description_2,
                     :trust_score_2, :verdict_2, :verdict_reasoning_2, :confidence_2,
                     :last_assessed_2, :first_seen_2, :last_seen_2, :last_scanned_2,
                     :scan_count_2, :risk_tier_2, :meta_2),
                    (:server_id_3, :name_3, :registry_source_3, :url_3, :description_3,
                     :trust_score_3, :verdict_3, :verdict_reasoning_3, :confidence_3,
                     :last_assessed_3, :first_seen_3, :last_seen_3, :last_scanned_3,
                     :scan_count_3, :risk_tier_3, :meta_3)
                """
            ),
            {
                "server_id": "server-1",
                "name": "Server One",
                "registry_source": "test",
                "url": "http://one",
                "description": "one",
                "trust_score": 0.9,
                "verdict": "allow",
                "verdict_reasoning": "test",
                "confidence": 0.9,
                "last_assessed": dates[0].isoformat(),
                "first_seen": dates[0].isoformat(),
                "last_seen": dates[0].isoformat(),
                "last_scanned": dates[0].isoformat(),
                "scan_count": 1,
                "risk_tier": "HIGH_RISK_ISOLATED",
                "meta": None,
                "server_id_2": "server-2",
                "name_2": "Server Two",
                "registry_source_2": "test",
                "url_2": "http://two",
                "description_2": "two",
                "trust_score_2": 0.9,
                "verdict_2": "allow",
                "verdict_reasoning_2": "test",
                "confidence_2": 0.9,
                "last_assessed_2": dates[0].isoformat(),
                "first_seen_2": dates[0].isoformat(),
                "last_seen_2": dates[0].isoformat(),
                "last_scanned_2": dates[0].isoformat(),
                "scan_count_2": 1,
                "risk_tier_2": "HIGH_RISK_ISOLATED",
                "meta_2": None,
                "server_id_3": "server-3",
                "name_3": "Server Three",
                "registry_source_3": "test",
                "url_3": "http://three",
                "description_3": "three",
                "trust_score_3": 0.8,
                "verdict_3": "allow",
                "verdict_reasoning_3": "test",
                "confidence_3": 0.8,
                "last_assessed_3": dates[1].isoformat(),
                "first_seen_3": dates[1].isoformat(),
                "last_seen_3": dates[1].isoformat(),
                "last_scanned_3": dates[1].isoformat(),
                "scan_count_3": 1,
                "risk_tier_3": "TRUSTED_GENERAL",
                "meta_3": None,
            },
        )

    def override_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/registry/tier-churn")
    def tier_churn(
        days: int = Query(default=7, ge=1, le=365),
        session: Session = Depends(get_session),
    ):
        return compute_tier_churn(session=session, days=days)

    test_app.dependency_overrides[get_session] = override_session
    response = TestClient(test_app).get("/api/registry/tier-churn?days=7")

    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == 7
    assert sum(payload["tier_counts"].values()) == 3
    assert [item["date"] for item in payload["churn"]] == [
        dates[0].isoformat(),
        dates[1].isoformat(),
    ]

    print("PASS")