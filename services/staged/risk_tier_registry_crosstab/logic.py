from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from app.db import get_session
from app.models import McpServerRegistry


_RISK_TIERS = (
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
    "UNKNOWN",
)


def _normalise_tier(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    tier = str(value).strip()
    return tier if tier in _RISK_TIERS else "UNKNOWN"


def _normalise_source(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    source = str(value).strip()
    return source if source else "UNKNOWN"


def get_risk_tier_crosstab(
    source: Optional[str] = None,
    session: Any = Depends(get_session),
) -> Dict[str, Any]:
    query = session.query(
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
    )
    if source is not None:
        query = query.filter(McpServerRegistry.registry_source == source)

    rows = query.all()

    counts: Dict[str, Dict[str, int]] = {
        tier: {} for tier in _RISK_TIERS
    }
    source_names = set()

    for row in rows:
        registry_source, risk_tier = row
        source_name = _normalise_source(registry_source)
        tier_name = _normalise_tier(risk_tier)
        source_names.add(source_name)
        counts[tier_name][source_name] = counts[tier_name].get(source_name, 0) + 1

    ordered_sources = sorted(source_names)
    ordered_sources.append("total")

    cells: List[List[int]] = []
    totals: Dict[str, int] = {}

    for tier in _RISK_TIERS:
        tier_counts = counts[tier]
        row_values = [tier_counts.get(source_name, 0) for source_name in ordered_sources[:-1]]
        tier_total = sum(tier_counts.values())
        row_values.append(tier_total)
        cells.append(row_values)
        totals[tier] = tier_total

    return {
        "sources": ordered_sources,
        "tiers": list(_RISK_TIERS),
        "cells": cells,
        "totals": totals,
        "grand_total": sum(totals.values()),
    }


def get_risk_tier_registry_crosstab(
    source: Optional[str] = None,
    session: Any = Depends(get_session),
) -> Dict[str, Any]:
    return get_risk_tier_crosstab(source=source, session=session)


def risk_tier_registry_crosstab(
    source: Optional[str] = None,
    session: Any = Depends(get_session),
) -> Dict[str, Any]:
    return get_risk_tier_crosstab(source=source, session=session)


def get_crosstab(
    source: Optional[str] = None,
    session: Any = Depends(get_session),
) -> Dict[str, Any]:
    return get_risk_tier_crosstab(source=source, session=session)


if __name__ == "__main__":
    class _SelfTestQuery:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            return self._rows

    class _SelfTestSession:
        def __init__(self, rows):
            self._rows = rows

        def query(self, *_columns):
            return _SelfTestQuery(self._rows)

    test_rows = [
        ("source_a", "TRUSTED_GENERAL"),
        ("source_a", "UNKNOWN"),
        ("source_b", "TRUSTED_GENERAL"),
        ("source_c", "KNOWN_THREAT"),
        ("source_c", None),
    ]
    test_session = _SelfTestSession(test_rows)
    test_app = FastAPI()

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/risk-tier-crosstab")
    def _test_route(source: Optional[str] = None, session: Any = Depends(get_session)):
        return get_risk_tier_crosstab(source=source, session=session)

    result = get_risk_tier_crosstab(session=test_session)
    assert result["grand_total"] == len(test_rows)
    assert result["totals"]["UNKNOWN"] == 2
    assert result["cells"][result["tiers"].index("TRUSTED_GENERAL")][
        result["sources"].index("source_a")
    ] == 1
    print("PASS")