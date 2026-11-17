from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.db import get_session  # noqa: F401
from app.models import VulnAdvisory, VulnLink  # noqa: F401


BUCKET_BOUNDS: List[tuple[float, float]] = [
    (0.0, 0.25),
    (0.25, 0.5),
    (0.5, 0.75),
    (0.75, 1.0),
]


def _bucket_for_confidence(confidence: float) -> int:
    """Return the fixed confidence bucket index for a value in [0, 1]."""
    normalized = min(1.0, max(0.0, float(confidence)))
    for index, (lower, upper) in enumerate(BUCKET_BOUNDS):
        if normalized < upper or (index == len(BUCKET_BOUNDS) - 1 and normalized == upper):
            return index
    return len(BUCKET_BOUNDS) - 1


def compute_confidence_histogram(
    session: Any,
    min_confidence: float = 0.0,
    basis: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute a confidence histogram and basis summary for ``vuln_links``.

    The query is portable between PostgreSQL and SQLite. Bucket construction is
    intentionally performed in Python rather than using PostgreSQL-specific
    ``width_bucket`` behavior.
    """
    parameters: Dict[str, Any] = {"min_confidence": float(min_confidence)}
    basis_clause = ""

    if basis is not None:
        parameters["basis"] = basis
        basis_clause = " AND vl.match_basis = :basis"

    statement = text(
        f"""
        SELECT
            vl.match_confidence AS match_confidence,
            vl.match_basis AS match_basis,
            CASE WHEN va.id IS NULL THEN 1 ELSE 0 END AS is_orphan
        FROM vuln_links AS vl
        LEFT JOIN vuln_advisories AS va
            ON va.id = vl.advisory_id
        WHERE vl.match_confidence >= :min_confidence
        {basis_clause}
        """
    )
    rows = session.execute(statement, parameters).mappings().all()

    bucket_counts = [0 for _ in BUCKET_BOUNDS]
    basis_counts: Dict[str, int] = defaultdict(int)
    basis_confidence_sums: Dict[str, float] = defaultdict(float)
    total_links = 0
    orphan_links = 0

    for row in rows:
        raw_confidence = row["match_confidence"]
        if raw_confidence is None:
            continue

        confidence = float(raw_confidence)
        raw_basis = row["match_basis"]
        match_basis = "" if raw_basis is None else str(raw_basis)

        total_links += 1
        bucket_counts[_bucket_for_confidence(confidence)] += 1
        basis_counts[match_basis] += 1
        basis_confidence_sums[match_basis] += confidence

        if int(row["is_orphan"] or 0) == 1:
            orphan_links += 1

    buckets = [
        {
            "lower": lower,
            "upper": upper,
            "count": bucket_counts[index],
        }
        for index, (lower, upper) in enumerate(BUCKET_BOUNDS)
    ]

    by_basis = [
        {
            "match_basis": match_basis,
            "count": basis_counts[match_basis],
            "avg_confidence": (
                basis_confidence_sums[match_basis] / basis_counts[match_basis]
            ),
        }
        for match_basis in sorted(basis_counts)
    ]

    return {
        "min_confidence": float(min_confidence),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "buckets": buckets,
        "by_basis": by_basis,
        "total_links": total_links,
        "orphan_links": orphan_links,
    }


if __name__ == "__main__":
    assert _bucket_for_confidence(0.1) == 0
    assert _bucket_for_confidence(0.25) == 1
    assert _bucket_for_confidence(0.6) == 2
    assert _bucket_for_confidence(0.75) == 3
    assert _bucket_for_confidence(1.0) == 3
    print("PASS")