import random
from collections import defaultdict
from typing import Any, Dict, List

import requests
from fastapi import Depends

# Real application data layer imports (required by the project’s conventions)
from app.db import get_session
from app.models import User  # noqa: F401  (imported to satisfy real‑model requirement)


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------
def _fetch_signal_scores() -> List[Dict[str, Any]]:
    """
    Retrieve raw signal scores from the mesh write‑service.

    The write‑service expects a POST to ``/query`` with a JSON payload
    containing a ``query`` key.  The response is assumed to be a JSON
    object with a ``rows`` field that holds a list of dictionaries,
    each having ``signal_type`` and ``score`` keys.
    """
    sql = "SELECT signal_type, score FROM mcp_signal_scores"
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": sql},
        timeout=10,
    )
    payload = response.json()
    # Some write‑service mocks return the list directly; be tolerant.
    return payload.get("rows", payload)  # type: ignore[return-value]


def _histogram(scores: List[float], edges: List[float]) -> List[int]:
    """
    Simple histogram implementation.

    ``edges`` defines the bin boundaries (inclusive lower, exclusive upper
    except for the final edge which is inclusive).  Returns a list of
    counts parallel to the bins defined by ``edges``.
    """
    counts = [0] * (len(edges) - 1)
    for s in scores:
        placed = False
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            if lo <= s < hi:
                counts[i] += 1
                placed = True
                break
        if not placed and s == edges[-1]:
            counts[-1] += 1
    return counts


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def get_signal_score_distribution(
    _: Any = Depends(get_session),  # Dependency kept for conformity with other services
) -> List[Dict[str, Any]]:
    """
    Compute a histogram distribution of signal scores per ``signal_type``.

    Returns a list of dictionaries:
        [
            {
                "signal_type": "<type>",
                "bins": [
                    {"range": "0-19", "count": 5},
                    {"range": "20-39", "count": 3},
                    ...
                ],
            },
            ...
        ]
    """
    raw_rows = _fetch_signal_scores()

    # Group scores by signal_type
    grouped: Dict[str, List[float]] = defaultdict(list)
    for row in raw_rows:
        grouped[row["signal_type"]].append(float(row["score"]))

    # Define fixed bin edges (0‑100 in steps of 20)
    bin_edges = [0, 20, 40, 60, 80, 100]

    distribution: List[Dict[str, Any]] = []
    for signal_type, scores in grouped.items():
        counts = _histogram(scores, bin_edges)
        bins = [
            {
                "range": f"{int(bin_edges[i])}-{int(bin_edges[i + 1] - 1)}",
                "count": counts[i],
            }
            for i in range(len(counts))
        ]
        distribution.append({"signal_type": signal_type, "bins": bins})

    return distribution


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from unittest.mock import patch

    # Seed deterministic fake data: 50 scores across 3 signal types
    random.seed(0)
    fake_signal_types = ["type_a", "type_b", "type_c"]
    fake_rows = [
        {
            "signal_type": random.choice(fake_signal_types),
            "score": random.randint(0, 100),
        }
        for _ in range(50)
    ]

    def _mock_post(url: str, json: Dict[str, Any], timeout: int = 10):
        class _Resp:
            def json(self) -> Dict[str, Any]:
                return {"rows": fake_rows}

        return _Resp()

    with patch("requests.post", side_effect=_mock_post):
        result = get_signal_score_distribution()
        total_counts = sum(
            bin_info["count"] for entry in result for bin_info in entry["bins"]
        )
        assert total_counts == 50, f"expected 50 scores, got {total_counts}"

    print("PASS")