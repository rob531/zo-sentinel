from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import Table, literal, select
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


RISK_TIERS = ("LOW", "MEDIUM", "HIGH")
UNKNOWN_TIER = "UNKNOWN"


class TierCounts(BaseModel):
    by_tier: Dict[str, int]
    total: int


class SourceTierCounts(BaseModel):
    registry_source: str
    by_tier: Dict[str, int]
    total: int


class TimelineTierCounts(BaseModel):
    date: str
    by_tier: Dict[str, int]
    total: int


class TierDistributionResponse(BaseModel):
    days: int
    as_of: str
    current: TierCounts
    by_source: List[SourceTierCounts]
    timeline: List[TimelineTierCounts]


def normalize_tier(value: Any) -> str:
    if value is None:
        return UNKNOWN_TIER
    tier = str(value).strip().upper()
    return tier if tier in RISK_TIERS else UNKNOWN_TIER


def aggregate_tier_rows(rows: Iterable[Tuple[Any, Any]]) -> Dict[str, Any]:
    counts = {tier: 0 for tier in RISK_TIERS}
    materialized = list(rows)
    for tier, _ in materialized:
        counts[normalize_tier(tier)] += 1
    return {"by_tier": counts, "total": len(materialized)}


def _column_names(bind: Any, table_name: str) -> Dict[str, str]:
    try:
        columns = inspect(bind).get_columns(table_name)
    except Exception:
        return {}
    return {str(column["name"]).lower(): str(column["name"]) for column in columns}


def _first_column(
    names: Dict[str, str], candidates: Tuple[str, ...]
) -> Optional[str]:
    for candidate in candidates:
        actual = names.get(candidate.lower())
        if actual:
            return actual
    return None


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        text = str(value).strip()
        if len(text) >= 10:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                pass
    return date.today()


def _timestamp_key(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return "" if value is None else str(value)


def _empty_tier_counts() -> Dict[str, int]:
    return {tier: 0 for tier in RISK_TIERS}


router = APIRouter(prefix="/api")


@router.get(
    "/risk/register/tier-distribution",
    response_model=TierDistributionResponse,
)
def tier_distribution(
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> TierDistributionResponse:
    bind = session.get_bind()
    registry_names = _column_names(bind, McpServerRegistry.__tablename__)
    risk_names = _column_names(bind, "mcp_risk_register")

    risk_table: Optional[Table] = None
    if risk_names:
        risk_table = Table(
            "mcp_risk_register",
            McpServerRegistry.metadata,
            autoload_with=bind,
            extend_existing=True,
        )

    registry_tier_name = _first_column(
        registry_names, ("risk_tier", "verdict")
    )
    registry_source_name = _first_column(registry_names, ("registry_source",))
    registry_date_name = _first_column(
        registry_names,
        ("last_assessed", "last_seen", "first_seen"),
    )
    risk_tier_name = _first_column(risk_names, ("risk_tier", "verdict"))
    risk_date_name = _first_column(
        risk_names,
        ("computed_at", "created_at", "assessed_at", "updated_at"),
    )

    registry_tier = (
        getattr(McpServerRegistry, registry_tier_name)
        if registry_tier_name
        else literal(None)
    )
    registry_source = (
        getattr(McpServerRegistry, registry_source_name)
        if registry_source_name
        else literal("unknown")
    )

    if risk_table is not None and risk_tier_name:
        risk_tier: Any = risk_table.c[risk_tier_name]
    else:
        risk_tier = literal(None)

    if risk_table is not None and risk_date_name:
        risk_date: Any = risk_table.c[risk_date_name]
    elif registry_date_name:
        risk_date = getattr(McpServerRegistry, registry_date_name)
    else:
        risk_date = literal(date.today())

    if risk_table is not None:
        statement = (
            select(
                McpServerRegistry.server_id.label("registry_server_id"),
                registry_tier.label("registry_tier"),
                registry_source.label("registry_source"),
                risk_table.c.server_id.label("risk_server_id"),
                risk_tier.label("risk_tier"),
                risk_date.label("computed_at"),
            )
            .select_from(
                McpServerRegistry.__table__.outerjoin(
                    risk_table,
                    McpServerRegistry.server_id == risk_table.c.server_id,
                )
            )
        )
        rows = session.execute(statement).mappings().all()
    else:
        statement = select(
            McpServerRegistry.server_id.label("registry_server_id"),
            registry_tier.label("registry_tier"),
            registry_source.label("registry_source"),
            literal(None).label("risk_server_id"),
            literal(None).label("risk_tier"),
            literal(None).label("computed_at"),
        ).select_from(McpServerRegistry.__table__)
        rows = session.execute(statement).mappings().all()

    current_by_server: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    timeline_rows: List[Dict[str, Any]] = []
    registry_servers: Dict[Any, Dict[str, Any]] = {}

    for row in rows:
        server_id = row["registry_server_id"]
        registry_record = {
            "server_id": server_id,
            "tier": row["registry_tier"],
            "source": row["registry_source"] or "unknown",
        }
        registry_servers[server_id] = registry_record

        risk_record = {
            "server_id": row["risk_server_id"],
            "tier": row["risk_tier"],
            "date": _as_date(row["computed_at"]),
            "timestamp": _timestamp_key(row["computed_at"]),
            "registry_tier": row["registry_tier"],
        }
        if row["risk_server_id"] is not None:
            current_by_server[server_id].append(risk_record)
            timeline_rows.append(risk_record)

    current_counts = _empty_tier_counts()
    source_counts: Dict[str, Dict[str, int]] = defaultdict(_empty_tier_counts)

    for server_id, registry_record in registry_servers.items():
        history = current_by_server.get(server_id, [])
        if history:
            latest = max(
                history,
                key=lambda item: (item["date"], item["timestamp"]),
            )
            raw_tier = latest["tier"]
            tier = (
                normalize_tier(raw_tier)
                if raw_tier is not None
                else normalize_tier(registry_record["tier"])
            )
        else:
            tier = normalize_tier(registry_record["tier"])

        current_counts[tier] += 1
        source_counts[str(registry_record["source"])][tier] += 1

    grouped_timeline: Dict[date, Dict[Any, Dict[str, Any]]] = defaultdict(dict)
    for record in timeline_rows:
        server_id = record["server_id"]
        existing = grouped_timeline[record["date"]].get(server_id)
        if existing is None or record["timestamp"] >= existing["timestamp"]:
            grouped_timeline[record["date"]][server_id] = record

    timeline: List[Dict[str, Any]] = []
    for timeline_date in sorted(grouped_timeline):
        records = list(grouped_timeline[timeline_date].values())
        aggregate = aggregate_tier_rows(
            (
                (
                    record["tier"]
                    if record["tier"] is not None
                    else record["registry_tier"],
                    timeline_date,
                )
                for record in records
            )
        )
        timeline.append(
            {
                "date": timeline_date.isoformat(),
                "by_tier": aggregate["by_tier"],
                "total": aggregate["total"],
            }
        )

    by_source = [
        {
            "registry_source": source,
            "by_tier": counts,
            "total": sum(counts.values()),
        }
        for source, counts in sorted(source_counts.items())
    ]

    return TierDistributionResponse(
        days=days,
        as_of=datetime.now(timezone.utc).isoformat(),
        current={
            "by_tier": current_counts,
            "total": sum(current_counts.values()),
        },
        by_source=by_source,
        timeline=timeline,
    )


app = FastAPI(title="Risk Register Tier Distribution API")
app.include_router(router)


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    risk_tier VARCHAR,
                    verdict VARCHAR,
                    registry_source VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_risk_register (
                    server_id VARCHAR NOT NULL,
                    risk_tier VARCHAR,
                    verdict VARCHAR,
                    computed_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, risk_tier, verdict, registry_source)
                VALUES
                    (:server_id, :risk_tier, :verdict, :registry_source)
                """
            ),
            [
                {
                    "server_id": "server-a",
                    "risk_tier": "MEDIUM",
                    "verdict": "MEDIUM",
                    "registry_source": "npm",
                },
                {
                    "server_id": "server-b",
                    "risk_tier": "HIGH",
                    "verdict": "HIGH",
                    "registry_source": "npm",
                },
                {
                    "server_id": "server-c",
                    "risk_tier": "LOW",
                    "verdict": "LOW",
                    "registry_source": "pypi",
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_risk_register
                    (server_id, risk_tier, verdict, computed_at)
                VALUES
                    (:server_id, :risk_tier, :verdict, :computed_at)
                """
            ),
            [
                {
                    "server_id": "server-a",
                    "risk_tier": "LOW",
                    "verdict": "LOW",
                    "computed_at": "2026-10-01T00:00:00",
                },
                {
                    "server_id": "server-b",
                    "risk_tier": "MEDIUM",
                    "verdict": "MEDIUM",
                    "computed_at": "2026-10-01T00:00:00",
                },
                {
                    "server_id": "server-c",
                    "risk_tier": "HIGH",
                    "verdict": "HIGH",
                    "computed_at": "2026-10-01T00:00:00",
                },
                {
                    "server_id": "server-a",
                    "risk_tier": "MEDIUM",
                    "verdict": "MEDIUM",
                    "computed_at": "2026-10-02T00:00:00",
                },
                {
                    "server_id": "server-b",
                    "risk_tier": "HIGH",
                    "verdict": "HIGH",
                    "computed_at": "2026-10-02T00:00:00",
                },
                {
                    "server_id": "server-c",
                    "risk_tier": "LOW",
                    "verdict": "LOW",
                    "computed_at": "2026-10-02T00:00:00",
                },
            ],
        )

    def override_get_session():
        test_session = test_session_factory()
        try:
            yield test_session
        finally:
            test_session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/risk/register/tier-distribution?days=30")
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"days", "as_of", "current", "by_source", "timeline"}
        assert payload["current"]["by_tier"] == {
            "LOW": 1,
            "MEDIUM": 1,
            "HIGH": 1,
        }
        assert payload["current"]["total"] == 3
        assert len(payload["timeline"]) == 2
        assert [entry["date"] for entry in payload["timeline"]] == [
            "2026-10-01",
            "2026-10-02",
        ]
        assert all(entry["total"] == 3 for entry in payload["timeline"])
        assert payload["by_source"]

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()