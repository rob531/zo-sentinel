from typing import Any, Dict, List, Optional

from fastapi import Depends

from app.db import get_session
from app.models import McpServerRegistry


_BANDS = (
    ("0", 0, 0),
    ("1", 1, 1),
    ("2", 2, 2),
    ("3-5", 3, 5),
    ("6-10", 6, 10),
    ("11-25", 11, 25),
    ("26+", 26, None),
)


def _median(values: List[int]) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)
    midpoint = len(ordered) // 2

    if len(ordered) % 2:
        return float(ordered[midpoint])

    return float((ordered[midpoint - 1] + ordered[midpoint]) / 2)


def get_scan_count_bands(
    db: Any = Depends(get_session),
    registry_source: Optional[str] = None,
    session: Any = None,
) -> Dict[str, Any]:
    database = session if session is not None else db

    query = database.query(
        McpServerRegistry.scan_count,
        McpServerRegistry.last_scanned,
    )
    if registry_source is not None:
        query = query.filter(
            McpServerRegistry.registry_source == registry_source
        )

    rows = query.all()
    scan_counts = [
        max(0, int(row.scan_count or 0))
        for row in rows
    ]

    total_servers = len(scan_counts)
    never_scanned = sum(1 for count in scan_counts if count == 0)

    bands: List[Dict[str, Any]] = []
    for band, lower, upper in _BANDS:
        if upper is None:
            server_count = sum(1 for count in scan_counts if count >= lower)
        else:
            server_count = sum(
                1 for count in scan_counts if lower <= count <= upper
            )

        pct = (
            float(server_count) * 100.0 / total_servers
            if total_servers
            else 0.0
        )
        bands.append(
            {
                "band": band,
                "lower": lower,
                "upper": upper,
                "server_count": server_count,
                "pct": pct,
            }
        )

    return {
        "total_servers": total_servers,
        "never_scanned": never_scanned,
        "bands": bands,
        "median_scan_count": _median(scan_counts),
    }


get_registry_scan_count_bands = get_scan_count_bands
scan_count_bands = get_scan_count_bands


__all__ = [
    "get_scan_count_bands",
    "get_registry_scan_count_bands",
    "scan_count_bands",
]