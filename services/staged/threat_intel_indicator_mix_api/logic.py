from app.db import get_session
from app.models import ThreatIntelRef
from sqlalchemy import func, select
from datetime import datetime, timedelta, timezone


def get_indicator_mix(session):
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=7)

    rows = session.execute(
        select(
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.indicator_value,
            ThreatIntelRef.source,
            ThreatIntelRef.is_aggregator,
            ThreatIntelRef.fetched_at,
        )
    ).all()

    by_indicator_type = {}
    by_source = {}
    aggregator_rows = 0
    stale_rows = 0

    for indicator_type, indicator_value, source, is_aggregator, fetched_at in rows:
        type_key = indicator_type or ""
        type_stats = by_indicator_type.setdefault(
            type_key, {"rows": 0, "values": set()}
        )
        type_stats["rows"] += 1
        type_stats["values"].add(indicator_value)

        source_key = source or ""
        by_source[source_key] = by_source.get(source_key, 0) + 1

        if is_aggregator:
            aggregator_rows += 1

        if fetched_at is not None:
            fetched_at_utc = fetched_at
            if fetched_at_utc.tzinfo is None:
                fetched_at_utc = fetched_at_utc.replace(tzinfo=timezone.utc)
            if fetched_at_utc < stale_cutoff:
                stale_rows += 1

    return {
        "by_indicator_type": [
            {
                "indicator_type": indicator_type,
                "rows": stats["rows"],
                "distinct_values": len(stats["values"]),
            }
            for indicator_type, stats in sorted(by_indicator_type.items())
        ],
        "by_source": [
            {"source": source, "rows": row_count}
            for source, row_count in sorted(by_source.items())
        ],
        "aggregator_rows": aggregator_rows,
        "stale_rows": stale_rows,
        "total_rows": len(rows),
        "generated_at": now,
    }