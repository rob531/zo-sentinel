from typing import Any

from fastapi import Depends
from sqlalchemy import case, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import VulnAdvisory


def _ecosystem_distribution_statement():
    ecosystem = func.coalesce(VulnAdvisory.ecosystem, "unknown").label("ecosystem")
    severity = func.lower(VulnAdvisory.severity)

    return (
        select(
            ecosystem,
            func.count(VulnAdvisory.id).label("count"),
            func.sum(case((severity == "critical", 1), else_=0)).label("critical"),
            func.sum(case((severity == "high", 1), else_=0)).label("high"),
            func.sum(case((severity == "medium", 1), else_=0)).label("medium"),
            func.sum(case((severity == "low", 1), else_=0)).label("low"),
            func.max(VulnAdvisory.published_at).label("newest_published_at"),
            func.min(VulnAdvisory.published_at).label("oldest_published_at"),
        )
        .group_by(ecosystem)
        .order_by(ecosystem)
    )


async def get_ecosystem_distribution(
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    result = await session.execute(_ecosystem_distribution_statement())
    return [dict(row) for row in result.mappings()]


get_advisory_ecosystem_distribution = get_ecosystem_distribution
get_advisory_ecosystems = get_ecosystem_distribution
get_vuln_advisory_ecosystem_distribution = get_ecosystem_distribution


async def get_ecosystem_distribution_payload(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ecosystems = await get_ecosystem_distribution(session)
    return {
        "total": sum(int(row["count"]) for row in ecosystems),
        "ecosystems": ecosystems,
    }


def run_self_test() -> None:
    statement = _ecosystem_distribution_statement()
    sql = str(statement.compile(dialect=postgresql.dialect())).lower()
    assert "coalesce" in sql
    assert "case" in sql
    assert "group by" in sql
    assert "max" in sql and "min" in sql
    print("PASS")


if __name__ == "__main__":
    run_self_test()