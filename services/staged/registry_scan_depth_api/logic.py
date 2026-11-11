from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


class ScanDepthBucket(BaseModel):
    label: str
    min: int
    max: int
    count: int


class TopScannedServer(BaseModel):
    server_id: str
    name: Optional[str]
    scan_count: int
    last_scanned: Optional[str]


class ScanDepthResponse(BaseModel):
    generated_at: str
    total_servers: int
    never_scanned: int
    buckets: List[ScanDepthBucket]
    top_scanned: List[TopScannedServer]


def _iso8601(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _scan_count(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def build_scan_depth(rows: List[dict], bucket_size: int) -> dict:
    """Build the scan-depth response from registry rows.

    Null scan counts are treated as zero for bucketing and ranking, while the
    never-scanned total includes both null and zero counts.
    """
    bucket_size = max(1, min(100, int(bucket_size)))

    normalized_rows: List[Dict[str, Any]] = []
    bucket_counts: Dict[int, int] = {}
    never_scanned = 0
    maximum_bucket = 0

    for row in rows:
        raw_scan_count = row.get("scan_count")
        scan_count = _scan_count(raw_scan_count)

        if raw_scan_count is None or scan_count == 0:
            never_scanned += 1

        if scan_count == 0:
            bucket_number = 0
        else:
            bucket_number = ((scan_count - 1) // bucket_size) + 1

        bucket_counts[bucket_number] = bucket_counts.get(bucket_number, 0) + 1
        maximum_bucket = max(maximum_bucket, bucket_number)

        server_id = row.get("server_id")
        normalized_rows.append(
            {
                "server_id": "" if server_id is None else str(server_id),
                "name": row.get("name"),
                "scan_count": scan_count,
                "last_scanned": _iso8601(row.get("last_scanned")),
            }
        )

    buckets: List[Dict[str, Any]] = []
    for bucket_number in range(maximum_bucket + 1):
        if bucket_number == 0:
            label = "0"
            minimum = 0
            maximum = 0
        else:
            minimum = ((bucket_number - 1) * bucket_size) + 1
            maximum = bucket_number * bucket_size
            label = f"{minimum}-{maximum}"

        buckets.append(
            {
                "label": label,
                "min": minimum,
                "max": maximum,
                "count": bucket_counts.get(bucket_number, 0),
            }
        )

    top_scanned = sorted(
        normalized_rows,
        key=lambda row: (-row["scan_count"], row["server_id"]),
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_servers": len(normalized_rows),
        "never_scanned": never_scanned,
        "buckets": buckets,
        "top_scanned": top_scanned,
    }


@router.get("/registry/scan-depth", response_model=ScanDepthResponse)
def get_registry_scan_depth(
    registry_source: Optional[str] = Query(
        default=None,
        description="Filter results by registry source.",
    ),
    bucket_size: int = Query(
        default=5,
        description="Bucket width, clamped to the range 1 through 100.",
    ),
    session=Depends(get_session),
) -> dict:
    query = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.scan_count,
        McpServerRegistry.last_scanned,
    )

    if registry_source is not None:
        query = query.filter(McpServerRegistry.registry_source == registry_source)

    records = query.all()
    rows = [dict(record._mapping) for record in records]

    return build_scan_depth(rows, bucket_size)


if __name__ == "__main__":
    test_rows = [
        {"server_id": "one", "name": "One", "scan_count": 0, "last_scanned": None},
        {"server_id": "two", "name": "Two", "scan_count": 3, "last_scanned": None},
        {"server_id": "three", "name": "Three", "scan_count": 7, "last_scanned": None},
        {"server_id": "four", "name": "Four", "scan_count": 12, "last_scanned": None},
        {"server_id": "five", "name": "Five", "scan_count": None, "last_scanned": None},
    ]
    result = build_scan_depth(test_rows, 5)
    assert result["total_servers"] == 5
    assert result["never_scanned"] == 2
    assert sum(bucket["count"] for bucket in result["buckets"]) == 5
    assert [
        server["scan_count"] for server in result["top_scanned"]
    ] == sorted(
        (server["scan_count"] for server in result["top_scanned"]),
        reverse=True,
    )
    print("PASS")