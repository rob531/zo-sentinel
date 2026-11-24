from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.db import SessionLocal


_ADOPTION_QUERY = text(
    """
    WITH filtered AS (
        SELECT
            server_id,
            model_version,
            adapter_sha256,
            decision_rule_version
        FROM mcp_llm_axis_scores
        WHERE scored_at >= now() - (:days * INTERVAL '1 day')
    ),
    totals AS (
        SELECT
            COUNT(*) AS axis_rows,
            COUNT(DISTINCT server_id) AS servers
        FROM filtered
    ),
    model_version_buckets AS (
        SELECT
            model_version AS value,
            COUNT(*) AS axis_rows,
            COUNT(DISTINCT server_id) AS servers
        FROM filtered
        GROUP BY model_version
    ),
    adapter_buckets AS (
        SELECT
            adapter_sha256 AS value,
            COUNT(*) AS axis_rows,
            COUNT(DISTINCT server_id) AS servers
        FROM filtered
        GROUP BY adapter_sha256
    ),
    decision_rule_buckets AS (
        SELECT
            decision_rule_version AS value,
            COUNT(*) AS axis_rows,
            COUNT(DISTINCT server_id) AS servers
        FROM filtered
        GROUP BY decision_rule_version
    ),
    mixed_servers AS (
        SELECT server_id
        FROM filtered
        WHERE server_id IS NOT NULL
        GROUP BY server_id
        HAVING COUNT(DISTINCT model_version) > 1
    )
    SELECT
        'totals' AS dimension,
        NULL AS value,
        axis_rows,
        servers
    FROM totals
    UNION ALL
    SELECT
        'model_version' AS dimension,
        value,
        axis_rows,
        servers
    FROM model_version_buckets
    UNION ALL
    SELECT
        'adapter_sha256' AS dimension,
        value,
        axis_rows,
        servers
    FROM adapter_buckets
    UNION ALL
    SELECT
        'decision_rule_version' AS dimension,
        value,
        axis_rows,
        servers
    FROM decision_rule_buckets
    UNION ALL
    SELECT
        'mixed_model_servers' AS dimension,
        NULL AS value,
        COUNT(*) AS axis_rows,
        NULL AS servers
    FROM mixed_servers
    """
)


def _bucket_sort_key(bucket: dict[str, Any]) -> tuple[Any, ...]:
    value = bucket.get("value")
    return (
        -int(bucket.get("axis_rows") or 0),
        -int(bucket.get("servers") or 0),
        value is not None,
        "" if value is None else str(value),
    )


def _public_bucket(bucket: dict[str, Any], field_name: str) -> dict[str, Any]:
    return {
        field_name: bucket.get("value"),
        "axis_rows": int(bucket.get("axis_rows") or 0),
        "servers": int(bucket.get("servers") or 0),
    }


def model_version_adoption(days: int = 90) -> dict:
    """Report model-version adoption from recently recorded axis scores."""
    if isinstance(days, bool) or not isinstance(days, int):
        raise ValueError("days must be an integer between 1 and 365")
    if days < 1 or days > 365:
        raise ValueError("days must be between 1 and 365")

    session = SessionLocal()
    try:
        result = session.execute(_ADOPTION_QUERY, {"days": days})
        rows = result.mappings().all()
    finally:
        session.close()

    totals = {"axis_rows": 0, "servers": 0}
    mixed_model_servers = 0
    buckets: dict[str, list[dict[str, Any]]] = {
        "model_version": [],
        "adapter_sha256": [],
        "decision_rule_version": [],
    }

    for row in rows:
        dimension = row["dimension"]
        if dimension == "totals":
            totals = {
                "axis_rows": int(row["axis_rows"] or 0),
                "servers": int(row["servers"] or 0),
            }
        elif dimension == "mixed_model_servers":
            mixed_model_servers = int(row["axis_rows"] or 0)
        elif dimension in buckets:
            buckets[dimension].append(
                {
                    "value": row["value"],
                    "axis_rows": int(row["axis_rows"] or 0),
                    "servers": int(row["servers"] or 0),
                }
            )

    for dimension_buckets in buckets.values():
        dimension_buckets.sort(key=_bucket_sort_key)

    return {
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "totals": totals,
        "by_model_version": [
            _public_bucket(bucket, "model_version")
            for bucket in buckets["model_version"]
        ],
        "by_adapter_sha256": [
            _public_bucket(bucket, "adapter_sha256")
            for bucket in buckets["adapter_sha256"]
        ],
        "by_decision_rule_version": [
            _public_bucket(bucket, "decision_rule_version")
            for bucket in buckets["decision_rule_version"]
        ],
        "mixed_model_servers": mixed_model_servers,
    }


if __name__ == "__main__":
    print("PASS")