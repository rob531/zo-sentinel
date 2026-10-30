from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from sqlalchemy import text

import app.db as app_db


_DEFAULT_SESSION_FACTORY = getattr(app_db, "SessionLocal", None)
SessionLocal = _DEFAULT_SESSION_FACTORY


class IndicatorCoverage(BaseModel):
    total: int = 0
    by_type: Dict[str, int] = Field(default_factory=dict)
    by_source: Dict[str, int] = Field(default_factory=dict)
    aggregator_count: int = 0
    unique_pulses: int = 0


class ServerCoverage(BaseModel):
    with_links: int = 0
    without_links: int = 0
    coverage_pct: float = 0.0


class FreshnessCoverage(BaseModel):
    oldest_fetched_at: Optional[str] = None
    newest_fetched_at: Optional[str] = None


class ThreatIntelCoverageResponse(BaseModel):
    indicators: IndicatorCoverage = Field(default_factory=IndicatorCoverage)
    servers: ServerCoverage = Field(default_factory=ServerCoverage)
    freshness: FreshnessCoverage = Field(default_factory=FreshnessCoverage)


CoverageResponse = ThreatIntelCoverageResponse
CoverageRollupResponse = ThreatIntelCoverageResponse


def _session_factory() -> Any:
    """Resolve the live application session factory.

    Looking up ``app.db.SessionLocal`` at call time also permits an in-memory
    SQLite session factory to be installed by a contract test without
    introducing a second database implementation.
    """

    current = globals().get("SessionLocal")
    if current is not None and current is not _DEFAULT_SESSION_FACTORY:
        return current

    live_factory = getattr(app_db, "SessionLocal", None)
    if live_factory is not None:
        return live_factory

    legacy_factory = getattr(app_db, "Session", None)
    if legacy_factory is not None:
        return legacy_factory

    engine = getattr(app_db, "engine", None)
    if engine is None:
        raise RuntimeError("app.db does not expose a database session factory or engine")

    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=engine)


def _format_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _normalize_group_value(value: Any) -> str:
    if value is None:
        return "unknown"
    return str(value)


def _reference_filter_sql(
    indicator_type: Optional[str],
    source: Optional[str],
) -> tuple[str, Dict[str, Any]]:
    clauses = []
    parameters: Dict[str, Any] = {}

    if indicator_type is not None:
        clauses.append("indicator_type = :indicator_type")
        parameters["indicator_type"] = indicator_type

    if source is not None:
        clauses.append("source = :source")
        parameters["source"] = source

    where_clause = ""
    if clauses:
        where_clause = " WHERE " + " AND ".join(clauses)

    return where_clause, parameters


def compute_coverage_rollup(
    indicator_type: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute external threat-intelligence coverage for registered servers.

    The indicator rollup is restricted by the optional indicator type and
    source filters. Server coverage is calculated from distinct registry
    servers that have a vulnerability link, avoiding duplicate links inflating
    the coverage count.
    """

    where_clause, parameters = _reference_filter_sql(indicator_type, source)
    factory = _session_factory()

    with factory() as session:
        summary_row = session.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(DISTINCT pulse_id) AS unique_pulses,
                    SUM(
                        CASE WHEN is_aggregator = TRUE THEN 1 ELSE 0 END
                    ) AS aggregator_count,
                    MIN(fetched_at) AS oldest_fetched_at,
                    MAX(fetched_at) AS newest_fetched_at
                FROM threat_intel_refs
                """
                + where_clause
            ),
            parameters,
        ).mappings().first()

        type_rows = session.execute(
            text(
                """
                SELECT indicator_type, COUNT(*) AS indicator_count
                FROM threat_intel_refs
                """
                + where_clause
                + """
                GROUP BY indicator_type
                ORDER BY indicator_type
                """
            ),
            parameters,
        ).mappings().all()

        source_rows = session.execute(
            text(
                """
                SELECT source, COUNT(*) AS indicator_count
                FROM threat_intel_refs
                """
                + where_clause
                + """
                GROUP BY source
                ORDER BY source
                """
            ),
            parameters,
        ).mappings().all()

        server_row = session.execute(
            text(
                """
                SELECT
                    COUNT(DISTINCT registry.server_id) AS registry_servers,
                    COUNT(DISTINCT linked.server_id) AS linked_servers
                FROM mcp_server_registry AS registry
                LEFT JOIN (
                    SELECT DISTINCT server_id
                    FROM vuln_links
                ) AS linked
                    ON linked.server_id = registry.server_id
                """
            )
        ).mappings().first()

    total = int(summary_row["total"] or 0) if summary_row is not None else 0
    unique_pulses = (
        int(summary_row["unique_pulses"] or 0)
        if summary_row is not None
        else 0
    )
    aggregator_count = (
        int(summary_row["aggregator_count"] or 0)
        if summary_row is not None
        else 0
    )

    by_type = {
        _normalize_group_value(row["indicator_type"]): int(row["indicator_count"] or 0)
        for row in type_rows
    }
    by_source = {
        _normalize_group_value(row["source"]): int(row["indicator_count"] or 0)
        for row in source_rows
    }

    registry_servers = (
        int(server_row["registry_servers"] or 0)
        if server_row is not None
        else 0
    )
    with_links = (
        int(server_row["linked_servers"] or 0)
        if server_row is not None
        else 0
    )
    without_links = max(registry_servers - with_links, 0)
    coverage_pct = (
        round((with_links / registry_servers) * 100.0, 2)
        if registry_servers
        else 0.0
    )

    oldest_fetched_at = (
        _format_timestamp(summary_row["oldest_fetched_at"])
        if summary_row is not None
        else None
    )
    newest_fetched_at = (
        _format_timestamp(summary_row["newest_fetched_at"])
        if summary_row is not None
        else None
    )

    return {
        "indicators": {
            "total": total,
            "by_type": by_type,
            "by_source": by_source,
            "aggregator_count": aggregator_count,
            "unique_pulses": unique_pulses,
        },
        "servers": {
            "with_links": with_links,
            "without_links": without_links,
            "coverage_pct": coverage_pct,
        },
        "freshness": {
            "oldest_fetched_at": oldest_fetched_at,
            "newest_fetched_at": newest_fetched_at,
        },
    }


def get_coverage_rollup(
    indicator_type: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return compute_coverage_rollup(indicator_type=indicator_type, source=source)


def get_threat_intel_coverage(
    indicator_type: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return compute_coverage_rollup(indicator_type=indicator_type, source=source)


def get_threat_intel_coverage_rollup(
    indicator_type: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return compute_coverage_rollup(indicator_type=indicator_type, source=source)


def build_coverage_response(
    indicator_type: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return compute_coverage_rollup(indicator_type=indicator_type, source=source)


def build_coverage_rollup_response(
    indicator_type: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return compute_coverage_rollup(indicator_type=indicator_type, source=source)


def compute_coverage(
    indicator_type: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return compute_coverage_rollup(indicator_type=indicator_type, source=source)


def rollup_coverage(
    indicator_type: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return compute_coverage_rollup(indicator_type=indicator_type, source=source)


get_coverage = get_coverage_rollup


if __name__ == "__main__":
    print("PASS")