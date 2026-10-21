from pathlib import Path
import sys
from datetime import date, datetime, timezone
from typing import Any, Iterator, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.db import SessionLocal


router = APIRouter()


class AdvisoryAgeingResponse(BaseModel):
    total: int
    age_buckets: dict[str, int]
    ecosystems: list[dict[str, Any]]
    oldest_published_at: Optional[str]
    latest_fetched_at: Optional[str]
    unknown_published_at: int


def get_db() -> Iterator[Any]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _dialect_name(db: Any) -> str:
    try:
        bind = db.get_bind()
    except Exception:
        bind = getattr(db, "bind", None)
    dialect = getattr(bind, "dialect", None)
    return getattr(dialect, "name", "").lower()


def _parse_database_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise ValueError("Unsupported CURRENT_TIMESTAMP value")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sqlite_cutoffs(db: Any) -> dict[str, str]:
    database_now = db.execute(text("SELECT CURRENT_TIMESTAMP")).scalar_one()
    now = _parse_database_timestamp(database_now)
    return {
        "older_180d": (now.replace(tzinfo=None) - _days(180)).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        ),
        "older_90d": (now.replace(tzinfo=None) - _days(90)).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        ),
        "older_30d": (now.replace(tzinfo=None) - _days(30)).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        ),
    }


def _days(value: int):
    from datetime import timedelta

    return timedelta(days=value)


def _age_case(db: Any, cutoffs: Optional[dict[str, str]]) -> str:
    if _dialect_name(db) == "postgresql":
        return """
            CASE
                WHEN published_at IS NULL THEN 'unknown'
                WHEN published_at < CURRENT_TIMESTAMP - INTERVAL '180 days'
                    THEN 'older_180d'
                WHEN published_at < CURRENT_TIMESTAMP - INTERVAL '90 days'
                    THEN '90_180d'
                WHEN published_at < CURRENT_TIMESTAMP - INTERVAL '30 days'
                    THEN '30_90d'
                ELSE '0_30d'
            END
        """

    if cutoffs is None:
        raise ValueError("Cutoffs are required for non-PostgreSQL databases")

    return f"""
        CASE
            WHEN published_at IS NULL THEN 'unknown'
            WHEN published_at < :older_180d THEN 'older_180d'
            WHEN published_at < :older_90d THEN '90_180d'
            WHEN published_at < :older_30d THEN '30_90d'
            ELSE '0_30d'
        END
    """


def _where_clause(ecosystem: Optional[str]) -> str:
    if ecosystem is None:
        return ""
    return "WHERE ecosystem = :ecosystem"


def _to_iso8601(value: Any) -> Optional[str]:
    if value is None:
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        except ValueError:
            return stripped
    else:
        return str(value)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


@router.get(
    "/vuln/advisory-ageing",
    response_model=AdvisoryAgeingResponse,
    summary="Report vulnerability advisory ageing and ecosystem staleness",
)
def get_advisory_ageing(
    ecosystem: Optional[str] = Query(default=None),
    db: Any = Depends(get_db),
) -> AdvisoryAgeingResponse:
    where_clause = _where_clause(ecosystem)
    cutoffs = None if _dialect_name(db) == "postgresql" else _sqlite_cutoffs(db)
    age_case = _age_case(db, cutoffs)

    aggregate_sql = f"""
        WITH bucketed AS (
            SELECT
                ecosystem,
                published_at,
                fetched_at,
                {age_case} AS age_bucket
            FROM vuln_advisories
            {where_clause}
        )
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN age_bucket = 'older_180d' THEN 1 ELSE 0 END)
                AS older_180d,
            SUM(CASE WHEN age_bucket = '90_180d' THEN 1 ELSE 0 END)
                AS 90_180d,
            SUM(CASE WHEN age_bucket = '30_90d' THEN 1 ELSE 0 END)
                AS 30_90d,
            SUM(CASE WHEN age_bucket = '0_30d' THEN 1 ELSE 0 END)
                AS 0_30d,
            SUM(CASE WHEN age_bucket = 'unknown' THEN 1 ELSE 0 END)
                AS unknown_published_at,
            MIN(published_at) AS oldest_published_at,
            MAX(fetched_at) AS latest_fetched_at
        FROM bucketed
    """

    aggregate_params: dict[str, Any] = {}
    if ecosystem is not None:
        aggregate_params["ecosystem"] = ecosystem
    if cutoffs is not None:
        aggregate_params.update(cutoffs)

    aggregate = (
        db.execute(text(aggregate_sql), aggregate_params).mappings().first() or {}
    )

    ecosystem_sql = f"""
        WITH bucketed AS (
            SELECT
                ecosystem,
                {age_case} AS age_bucket
            FROM vuln_advisories
            {where_clause}
        )
        SELECT
            COALESCE(ecosystem, 'unknown') AS ecosystem,
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN age_bucket IN ('older_180d', '90_180d') THEN 1
                    ELSE 0
                END
            ) AS older_90d
        FROM bucketed
        GROUP BY COALESCE(ecosystem, 'unknown')
        ORDER BY older_90d DESC, total DESC, ecosystem ASC
    """

    ecosystem_rows = (
        db.execute(text(ecosystem_sql), aggregate_params).mappings().all()
    )

    ecosystems = [
        {
            "ecosystem": str(row["ecosystem"]),
            "total": int(row["total"] or 0),
            "older_90d": int(row["older_90d"] or 0),
        }
        for row in ecosystem_rows
    ]

    return AdvisoryAgeingResponse(
        total=int(aggregate.get("total") or 0),
        age_buckets={
            "older_180d": int(aggregate.get("older_180d") or 0),
            "90_180d": int(aggregate.get("90_180d") or 0),
            "30_90d": int(aggregate.get("30_90d") or 0),
            "0_30d": int(aggregate.get("0_30d") or 0),
        },
        ecosystems=ecosystems,
        oldest_published_at=_to_iso8601(aggregate.get("oldest_published_at")),
        latest_fetched_at=_to_iso8601(aggregate.get("latest_fetched_at")),
        unknown_published_at=int(aggregate.get("unknown_published_at") or 0),
    )


def _self_test() -> None:
    sample = AdvisoryAgeingResponse(
        total=4,
        age_buckets={
            "older_180d": 1,
            "90_180d": 1,
            "30_90d": 1,
            "0_30d": 1,
        },
        ecosystems=[
            {"ecosystem": "npm", "total": 2, "older_90d": 1},
            {"ecosystem": "pypi", "total": 2, "older_90d": 1},
        ],
        oldest_published_at="2024-01-01T00:00:00+00:00",
        latest_fetched_at="2024-01-02T00:00:00+00:00",
        unknown_published_at=1,
    )
    assert sample.total == 4
    assert sample.age_buckets["0_30d"] == 1
    assert sample.age_buckets["90_180d"] == 1
    assert sample.age_buckets["older_180d"] == 1
    assert sample.unknown_published_at == 1
    assert any(item["ecosystem"] == "npm" for item in sample.ecosystems)
    assert _to_iso8601(None) is None
    print("PASS")


if __name__ == "__main__":
    _self_test()