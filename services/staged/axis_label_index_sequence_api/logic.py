from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpLlmAxisScore


CANONICAL_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

_LABEL_ORDER: Dict[str, Tuple[str, ...]] = {
    "overall_risk": ("low", "moderate", "high", "critical"),
    "auth_strength": ("weak", "limited", "adequate", "strong", "robust"),
    "capability_breadth": ("minimal", "limited", "broad", "extensive"),
    "data_sensitivity": ("none", "low", "medium", "high", "critical"),
    "network_egress": ("none", "restricted", "moderate", "open", "unrestricted"),
    "maintainer_trust": ("untrusted", "unknown", "trusted", "strong"),
    "exploit_surface": ("minimal", "limited", "moderate", "high", "exposed"),
}

_GENERIC_LABEL_ORDER = (
    "none",
    "minimal",
    "negligible",
    "low",
    "safe",
    "weak",
    "insecure",
    "unknown",
    "limited",
    "moderate",
    "medium",
    "guarded",
    "restricted",
    "adequate",
    "fair",
    "broad",
    "elevated",
    "high",
    "danger",
    "severe",
    "exposed",
    "critical",
    "strong",
    "robust",
    "extensive",
)

_GENERIC_RANK = {label: index for index, label in enumerate(_GENERIC_LABEL_ORDER)}


def _label_rank(axis_name: str, label: Optional[str]) -> Optional[int]:
    if label is None:
        return None

    normalized = str(label).strip().lower().replace("-", "_").replace(" ", "_")
    axis_order = _LABEL_ORDER.get(axis_name)
    if axis_order:
        for index, candidate in enumerate(axis_order):
            if normalized == candidate:
                return index

    if normalized in _GENERIC_RANK:
        return _GENERIC_RANK[normalized]

    aliases = {
        "low_risk": "low",
        "medium_risk": "medium",
        "high_risk": "high",
        "critical_risk": "critical",
        "minimal_breadth": "minimal",
        "limited_breadth": "limited",
        "broad_breadth": "broad",
        "extensive_breadth": "extensive",
        "no_data": "none",
        "no_access": "none",
        "not_applicable": "none",
        "not_exposed": "minimal",
        "weak_auth": "weak",
        "strong_auth": "strong",
        "open_egress": "open",
        "unrestricted_egress": "unrestricted",
        "trusted_maintainer": "trusted",
        "untrusted_maintainer": "untrusted",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in _GENERIC_RANK:
        return _GENERIC_RANK[normalized]
    return None


def _row_value(row: Any, name: str) -> Any:
    if hasattr(row, name):
        return getattr(row, name)
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return None


def _distinct_pairs(
    rows: Iterable[Any],
) -> Tuple[List[Tuple[int, str]], Dict[Tuple[int, str], int]]:
    pairs: List[Tuple[int, str]] = []
    occurrences: Dict[Tuple[int, str], int] = {}

    for row in rows:
        index = _row_value(row, "label_index")
        label = _row_value(row, "label")
        if index is None or label is None:
            continue
        pair = (int(index), str(label))
        if pair not in occurrences:
            pairs.append(pair)
            occurrences[pair] = 0
        occurrences[pair] += 1

    return pairs, occurrences


def _ordering_violations(axis_name: str, pairs: Iterable[Tuple[int, str]]) -> int:
    violations = 0
    ranked_pairs = [
        (pair, _label_rank(axis_name, pair[1]))
        for pair in pairs
    ]

    for first, first_rank in ranked_pairs:
        if first_rank is None:
            continue
        for second, second_rank in ranked_pairs:
            if second_rank is None or first_rank == second_rank:
                continue
            if first_rank < second_rank and first[0] > second[0]:
                violations += 1

    return violations


async def get_axis_label_index_sequence(session: AsyncSession) -> Dict[str, Any]:
    statement = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
            McpLlmAxisScore.scored_at,
        )
        .where(McpLlmAxisScore.axis_name.in_(CANONICAL_AXES))
        .order_by(
            McpLlmAxisScore.scored_at.asc(),
            McpLlmAxisScore.id.asc(),
        )
    )
    result = await session.execute(statement)
    rows = result.all()

    grouped: Dict[str, List[Any]] = {axis: [] for axis in CANONICAL_AXES}
    for row in rows:
        axis_name = _row_value(row, "axis_name")
        if axis_name in grouped:
            grouped[axis_name].append(row)

    axes: List[Dict[str, Any]] = []
    for axis_name in CANONICAL_AXES:
        pairs, occurrences = _distinct_pairs(grouped[axis_name])
        axes.append(
            {
                "axis_name": axis_name,
                "labels": [
                    {
                        "label": label,
                        "label_index": label_index,
                        "occurrences": occurrences[(label_index, label)],
                    }
                    for label_index, label in pairs
                ],
                "ordering_violations": _ordering_violations(axis_name, pairs),
                "drift": _ordering_violations(axis_name, pairs) > 0,
            }
        )

    return {
        "axes": axes,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_axis_label_index_sequences(session: AsyncSession) -> Dict[str, Any]:
    return await get_axis_label_index_sequence(session)


if __name__ == "__main__":
    from types import SimpleNamespace

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    class _MemoryResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _MemorySession:
        def __init__(self, rows):
            self.rows = rows

        async def execute(self, statement):
            return _MemoryResult(self.rows)

    memory_session = _MemorySession(
        [
            SimpleNamespace(
                axis_name="overall_risk",
                label="low",
                label_index=2,
                scored_at=datetime.now(timezone.utc),
            ),
            SimpleNamespace(
                axis_name="overall_risk",
                label="critical",
                label_index=1,
                scored_at=datetime.now(timezone.utc),
            ),
            SimpleNamespace(
                axis_name="overall_risk",
                label="low",
                label_index=2,
                scored_at=datetime.now(timezone.utc),
            ),
        ]
    )

    async def override_session():
        yield memory_session

    test_app = FastAPI()

    @test_app.get("/api/axis-label-index-sequence")
    async def endpoint(session=Depends(get_session)):
        return await get_axis_label_index_sequence(session)

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis-label-index-sequence")
        assert response.status_code == 200, response.text
        payload = response.json()
        axis_names = {axis["axis_name"] for axis in payload["axes"]}
        assert set(CANONICAL_AXES).issubset(axis_names)
        overall = next(
            axis for axis in payload["axes"] if axis["axis_name"] == "overall_risk"
        )
        assert overall["ordering_violations"] >= 1
        assert overall["drift"] is True

    print("PASS")