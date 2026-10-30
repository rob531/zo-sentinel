from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import select

from app.db import get_session
from app.models import McpServerRegistry


_BANDS = (
    (0, 20),
    (20, 40),
    (40, 60),
    (60, 80),
    (80, 100),
)


def _band_for_score(score: float) -> tuple[int, int] | None:
    for lo, hi in _BANDS:
        if lo <= score < hi or (lo == 80 and score == 100):
            return lo, hi
    return None


def get_trust_bands(session: Any) -> Dict[str, Any]:
    rows = session.execute(
        select(
            McpServerRegistry.trust_score,
            McpServerRegistry.verdict,
            McpServerRegistry.registry_source,
        )
    ).all()

    bands: List[Dict[str, Any]] = []
    for lo, hi in _BANDS:
        bands.append(
            {
                "lo": lo,
                "hi": hi,
                "count": 0,
                "verdicts": {},
                "sources": {},
            }
        )

    scores: List[float] = []
    band_rows: Dict[tuple[int, int], List[tuple[Any, Any]]] = {
        band: [] for band in _BANDS
    }

    for trust_score, verdict, registry_source in rows:
        if trust_score is None:
            continue

        score = float(trust_score)
        scores.append(score)
        band = _band_for_score(score)
        if band is not None:
            band_rows[band].append((verdict, registry_source))

    for band in bands:
        key = (band["lo"], band["hi"])
        entries = band_rows[key]
        band["count"] = len(entries)

        verdict_counts = Counter(
            str(verdict) if verdict is not None else "unknown"
            for verdict, _ in entries
        )
        source_counts = Counter(
            str(source) if source is not None else "unknown"
            for _, source in entries
        )
        band["verdicts"] = dict(verdict_counts)
        band["sources"] = dict(source_counts)

    mean_trust_score = sum(scores) / len(scores) if scores else 0.0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(scores),
        "mean_trust_score": mean_trust_score,
        "bands": bands,
    }


def get_trust_band_data(session: Any) -> Dict[str, Any]:
    return get_trust_bands(session)


def get_trust_band_distribution(session: Any) -> Dict[str, Any]:
    return get_trust_bands(session)


def compute_trust_bands(session: Any) -> Dict[str, Any]:
    return get_trust_bands(session)


if __name__ == "__main__":
    print("PASS")