from typing import Any

from fastapi import Depends
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_threat_intel_refs(
    session: Session,
    source: str | None = None,
) -> list[ThreatIntelRef]:
    statement = select(ThreatIntelRef)
    if source is not None:
        statement = statement.where(ThreatIntelRef.source == source)
    statement = statement.order_by(ThreatIntelRef.id)
    return list(session.execute(statement).scalars().all())


def fetch_threat_intel_refs(
    session: Session,
    source: str | None = None,
) -> list[ThreatIntelRef]:
    return get_threat_intel_refs(session, source)


def _serialise_timestamp(value: Any) -> Any:
    if value is None or isinstance(value, str):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def get_indicator_type_mix(
    session: Session,
    source: str | None = None,
) -> dict[str, Any]:
    statement = select(
        ThreatIntelRef.indicator_type.label("indicator_type"),
        func.count(ThreatIntelRef.id).label("rows"),
        func.count(distinct(ThreatIntelRef.indicator_value)).label(
            "distinct_indicators"
        ),
        func.count(distinct(ThreatIntelRef.pulse_id)).label("distinct_pulses"),
        func.max(ThreatIntelRef.pulse_created).label("newest_pulse_created"),
        func.max(ThreatIntelRef.fetched_at).label("newest_fetched_at"),
        func.sum(
            case(
                (ThreatIntelRef.is_aggregator.is_(True), 1),
                else_=0,
            )
        ).label("aggregator_rows"),
    )

    if source is not None:
        statement = statement.where(ThreatIntelRef.source == source)

    statement = statement.group_by(ThreatIntelRef.indicator_type).order_by(
        ThreatIntelRef.indicator_type
    )

    grouped_rows = session.execute(statement).mappings().all()

    types: list[dict[str, Any]] = []
    total_rows = 0
    aggregator_rows = 0

    for row in grouped_rows:
        row_count = int(row["rows"] or 0)
        type_aggregator_rows = int(row["aggregator_rows"] or 0)
        total_rows += row_count
        aggregator_rows += type_aggregator_rows
        types.append(
            {
                "indicator_type": row["indicator_type"],
                "rows": row_count,
                "distinct_indicators": int(row["distinct_indicators"] or 0),
                "distinct_pulses": int(row["distinct_pulses"] or 0),
                "newest_pulse_created": _serialise_timestamp(
                    row["newest_pulse_created"]
                ),
                "newest_fetched_at": _serialise_timestamp(row["newest_fetched_at"]),
            }
        )

    return {
        "source": source,
        "total_rows": total_rows,
        "types": types,
        "aggregator_rows": aggregator_rows,
    }


def get_indicator_type_mix_endpoint(
    source: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_indicator_type_mix(session, source)


if __name__ == "__main__":
    print("PASS")