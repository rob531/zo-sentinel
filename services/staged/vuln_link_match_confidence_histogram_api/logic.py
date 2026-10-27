from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink


def get_match_confidence_histogram(
    session: Session = Depends(get_session),
    bins: int = 10,
) -> Dict[str, Any]:
    """Return the distribution of vulnerability-link match confidence."""
    if isinstance(session, int) and not isinstance(bins, int):
        session, bins = bins, session

    if bins < 1:
        raise ValueError("bins must be a positive integer")

    rows = session.execute(
        select(VulnLink.match_confidence, VulnLink.match_basis)
    ).all()

    total_links = len(rows)
    histogram = [
        {
            "bin_index": index,
            "lower": index / bins,
            "upper": (index + 1) / bins,
            "count": 0,
        }
        for index in range(bins)
    ]

    confidence_values: List[float] = []
    basis_values: Dict[str, List[float]] = {}
    basis_counts: Dict[str, int] = {}

    for confidence, basis in rows:
        basis_key = basis if isinstance(basis, str) else ("unknown" if basis is None else str(basis))
        basis_counts[basis_key] = basis_counts.get(basis_key, 0) + 1

        if confidence is None:
            continue

        value = float(confidence)
        confidence_values.append(value)
        basis_values.setdefault(basis_key, []).append(value)

        bin_index = int(value * bins)
        if bin_index >= bins:
            bin_index = bins - 1
        if bin_index < 0:
            bin_index = 0
        histogram[bin_index]["count"] += 1

    if confidence_values:
        min_confidence = min(confidence_values)
        max_confidence = max(confidence_values)
        avg_confidence = sum(confidence_values) / len(confidence_values)
    else:
        min_confidence = 0.0
        max_confidence = 0.0
        avg_confidence = 0.0

    by_basis = {
        basis: {
            "count": basis_counts[basis],
            "avg_confidence": (
                sum(basis_values[basis]) / len(basis_values[basis])
                if basis_values.get(basis)
                else 0.0
            ),
        }
        for basis in sorted(basis_counts)
    }

    return {
        "bins": bins,
        "histogram": histogram,
        "by_basis": by_basis,
        "total_links": total_links,
        "min_confidence": min_confidence,
        "max_confidence": max_confidence,
        "avg_confidence": avg_confidence,
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
                    advisory_id INTEGER,
                    server_id INTEGER,
                    match_basis VARCHAR,
                    match_value VARCHAR,
                    match_confidence FLOAT,
                    linked_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (id, advisory_id, server_id, match_basis, match_value,
                     match_confidence, linked_at)
                VALUES
                    (1, 1, 1, 'cve', 'CVE-2024-0001', 0.1, CURRENT_TIMESTAMP),
                    (2, 2, 1, 'cve', 'CVE-2024-0002', 0.45, CURRENT_TIMESTAMP),
                    (3, 3, 1, 'package', 'some-package', 0.7, CURRENT_TIMESTAMP),
                    (4, 4, 1, 'package', 'other-package', 0.95, CURRENT_TIMESTAMP)
                """
            )
        )

    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()
    local_app = FastAPI()

    def override_get_session():
        yield test_session

    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/vuln/links/match-confidence-histogram")
    def test_endpoint(
        bins: int = 4,
        session: Session = Depends(get_session),
    ):
        return get_match_confidence_histogram(bins=bins, session=session)

    result = get_match_confidence_histogram(bins=4, session=test_session)
    assert result["total_links"] == 4
    assert sum(item["count"] for item in result["histogram"]) == 4
    assert result["max_confidence"] == 0.95
    assert len(result["histogram"]) == 4
    assert len(result["by_basis"]) == 2

    test_session.close()
    print("PASS")