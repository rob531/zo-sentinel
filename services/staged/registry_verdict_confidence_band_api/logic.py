from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_CONFIDENCE_BANDS = ("lt_20", "20_40", "40_60", "60_80", "gte_80", "unknown")


def _confidence_band(confidence: Any) -> str:
    """Return the canonical confidence-band name for a confidence value."""
    if confidence is None:
        return "unknown"

    value = float(confidence)
    if value < 0.20:
        return "lt_20"
    if value < 0.40:
        return "20_40"
    if value < 0.60:
        return "40_60"
    if value < 0.80:
        return "60_80"
    return "gte_80"


def get_verdict_confidence_bands(db: Session = Depends(get_session)) -> dict[str, Any]:
    """Build the verdict/confidence-band summary from the app registry.

    The aggregation is performed in SQL so that it remains portable across
    PostgreSQL and the SQLAlchemy-backed test database. NULL confidence is
    explicitly represented by the ``unknown`` band.
    """
    statement = text(
        """
        SELECT
            verdict,
            CASE
                WHEN confidence IS NULL THEN 'unknown'
                WHEN confidence < 0.20 THEN 'lt_20'
                WHEN confidence < 0.40 THEN '20_40'
                WHEN confidence < 0.60 THEN '40_60'
                WHEN confidence < 0.80 THEN '60_80'
                ELSE 'gte_80'
            END AS band,
            COUNT(*) AS count
        FROM mcp_server_registry
        GROUP BY
            verdict,
            CASE
                WHEN confidence IS NULL THEN 'unknown'
                WHEN confidence < 0.20 THEN 'lt_20'
                WHEN confidence < 0.40 THEN '20_40'
                WHEN confidence < 0.60 THEN '40_60'
                WHEN confidence < 0.80 THEN '60_80'
                ELSE 'gte_80'
            END
        ORDER BY verdict, band
        """
    )

    rows = db.execute(statement).mappings().all()
    bands = [
        {
            "band": str(row["band"]),
            "verdict": str(row["verdict"]) if row["verdict"] is not None else "unknown",
            "count": int(row["count"]),
        }
        for row in rows
    ]

    band_order = {name: index for index, name in enumerate(_CONFIDENCE_BANDS)}
    bands.sort(key=lambda item: (item["verdict"], band_order.get(item["band"], len(band_order))))

    return {
        "total": sum(item["count"] for item in bands),
        "bands": bands,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# Compatibility aliases for callers using the shorter service-specific names.
get_confidence_bands = get_verdict_confidence_bands
get_registry_verdict_confidence_bands = get_verdict_confidence_bands


if __name__ == "__main__":
    assert _confidence_band(None) == "unknown"
    assert _confidence_band(0.05) == "lt_20"
    assert _confidence_band(0.20) == "20_40"
    assert _confidence_band(0.40) == "40_60"
    assert _confidence_band(0.60) == "60_80"
    assert _confidence_band(0.80) == "gte_80"
    assert _confidence_band(0.91) == "gte_80"
    print("PASS")