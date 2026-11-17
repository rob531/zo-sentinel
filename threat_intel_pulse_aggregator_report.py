from collections import Counter
from datetime import datetime, timedelta, timezone


STALE_AFTER_DAYS = 30
TOP_PULSE_LIMIT = 10


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _pulse_key(row: dict) -> object:
    pulse_id = row.get("pulse_id")
    if pulse_id not in (None, ""):
        return pulse_id
    return row.get("pulse_name", "")


def build_report(rows: list[dict], as_of: str | None = None) -> dict:
    reference_time = _parse_datetime(as_of) if as_of is not None else datetime.now(timezone.utc)
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    indicator_type_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    pulse_rows: dict[object, list[dict]] = {}
    pulse_names: dict[object, str] = {}
    aggregator_count = 0
    indicator_count = 0

    for row in rows:
        indicator_count += 1

        indicator_type = str(row.get("indicator_type") or "unknown")
        source = str(row.get("source") or "unknown")
        indicator_type_counts[indicator_type] += 1
        source_counts[source] += 1

        if _is_truthy(row.get("is_aggregator")):
            aggregator_count += 1

        key = _pulse_key(row)
        pulse_rows.setdefault(key, []).append(row)
        pulse_names.setdefault(key, str(row.get("pulse_name") or "unknown"))

    top_pulses = []
    stale_pulse_count = 0
    cutoff = reference_time - timedelta(days=STALE_AFTER_DAYS)

    for key, group in pulse_rows.items():
        latest_row = None
        latest_datetime = None

        for row in group:
            created = row.get("pulse_created")
            created_datetime = _parse_datetime(created)
            if created_datetime is not None and (
                latest_datetime is None or created_datetime > latest_datetime
            ):
                latest_datetime = created_datetime
                latest_row = row

        latest_pulse_created = (
            latest_row.get("pulse_created") if latest_row is not None else None
        )
        if latest_datetime is not None and latest_datetime < cutoff:
            stale_pulse_count += 1

        top_pulses.append(
            {
                "pulse_name": pulse_names[key],
                "indicator_count": len(group),
                "latest_pulse_created": latest_pulse_created,
            }
        )

    top_pulses.sort(
        key=lambda pulse: (
            pulse["indicator_count"],
            pulse["latest_pulse_created"] or "",
            pulse["pulse_name"],
        ),
        reverse=True,
    )
    top_pulses = top_pulses[:TOP_PULSE_LIMIT]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pulse_count": len(pulse_rows),
        "indicator_count": indicator_count,
        "by_indicator_type": dict(indicator_type_counts),
        "by_source": dict(source_counts),
        "aggregator_count": aggregator_count,
        "top_pulses": top_pulses,
        "stale_pulse_count": stale_pulse_count,
    }


if __name__ == "__main__":
    synthetic_rows = [
        {
            "pulse_id": "pulse-1",
            "pulse_name": "Example Pulse One",
            "pulse_created": "2025-01-15T10:00:00Z",
            "indicator_type": "ip",
            "indicator_value": "192.0.2.1",
            "is_aggregator": False,
            "source": "example-source",
            "source_url": "https://example.invalid/1",
        },
        {
            "pulse_id": "pulse-1",
            "pulse_name": "Example Pulse One",
            "pulse_created": "2025-01-16T10:00:00Z",
            "indicator_type": "domain",
            "indicator_value": "example.invalid",
            "is_aggregator": True,
            "source": "example-source",
            "source_url": "https://example.invalid/2",
        },
        {
            "pulse_id": "pulse-2",
            "pulse_name": "Example Pulse Two",
            "pulse_created": "2025-01-17T10:00:00Z",
            "indicator_type": "url",
            "indicator_value": "https://example.invalid/indicator",
            "is_aggregator": False,
            "source": "second-source",
            "source_url": "https://example.invalid/3",
        },
    ]

    report = build_report(synthetic_rows, as_of="2025-01-20T00:00:00Z")
    assert report["pulse_count"] == 2
    assert report["indicator_count"] == 3
    assert report["aggregator_count"] == 1
    assert set(report["by_indicator_type"]) == {"ip", "domain", "url"}
    print("PASS")