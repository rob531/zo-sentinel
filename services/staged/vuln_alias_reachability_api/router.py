from __future__ import annotations

import json
import statistics
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

# Import the service's real data-layer implementation using a relative import.
# The pure summarization implementation below intentionally remains independent of
# the database so that it can be tested without a live session.
from .logic import *  # noqa: F401,F403


router = APIRouter(prefix="/api")


class MatchBasisCount(BaseModel):
    basis: str
    count: int


class EcosystemReachability(BaseModel):
    ecosystem: str
    feed: str
    advisory_count: int
    unreached_advisories: int
    no_alias_advisories: int
    link_count: int
    distinct_servers: int
    mean_match_confidence: float | None = None
    by_match_basis: list[MatchBasisCount] = Field(default_factory=list)


class ReachabilityTotals(BaseModel):
    advisories: int
    unreached: int
    links: int


class AliasReachabilityResponse(BaseModel):
    ecosystems: list[EcosystemReachability]
    totals: ReachabilityTotals


def _load_json(value: Any) -> Any:
    """Load a JSON column without assuming the driver's native JSON type."""
    if value is None:
        return []
    if isinstance(value, (list, dict, int, float, bool)):
        return value
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return []
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    return []


def _alias_list(value: Any) -> list[Any]:
    parsed = _load_json(value)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        # JSON objects are accepted defensively as well.  Their keys are the
        # only generally meaningful alias identifiers available in that form.
        return list(parsed.keys())
    if parsed is None:
        return []
    return [parsed]


def _confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence != confidence:  # NaN
        return None
    return confidence


def summarize_reachability(
    advisories: list[dict], links: list[dict]
) -> dict:
    """Return the API's reachability report without performing any I/O.

    The function accepts ordinary dictionaries, making it usable with rows
    returned by SQLAlchemy, dictionaries from a test fixture, or hand-built
    acceptance-test data.
    """
    advisory_rows = advisories or []
    link_rows = links or []

    advisory_by_id: dict[Any, dict] = {}
    groups: dict[tuple[str, str], dict[str, Any]] = {}

    for row in advisory_rows:
        advisory_id = row.get("id")
        feed = str(row.get("feed") or "")
        ecosystem = str(row.get("ecosystem") or "")
        key = (feed, ecosystem)

        group = groups.setdefault(
            key,
            {
                "ecosystem": ecosystem,
                "feed": feed,
                "advisory_count": 0,
                "unreached_advisories": 0,
                "no_alias_advisories": 0,
                "link_count": 0,
                "servers": set(),
                "confidences": [],
                "bases": {},
            },
        )
        group["advisory_count"] += 1

        aliases = _alias_list(row.get("aliases"))
        if not aliases:
            group["no_alias_advisories"] += 1

        if advisory_id is not None:
            advisory_by_id[advisory_id] = {
                "group": group,
                "server_ids": set(),
            }

    for row in link_rows:
        advisory_id = row.get("advisory_id")
        advisory = advisory_by_id.get(advisory_id)
        if advisory is None:
            continue

        group = advisory["group"]
        server_id = row.get("server_id")
        if server_id is not None:
            advisory["server_ids"].add(server_id)
            group["servers"].add(server_id)

        basis = str(row.get("match_basis") or "")
        group["bases"][basis] = group["bases"].get(basis, 0) + 1
        group["link_count"] += 1

        confidence = _confidence(row.get("match_confidence"))
        if confidence is not None:
            group["confidences"].append(confidence)

    for advisory in advisory_by_id.values():
        if not advisory["server_ids"]:
            advisory["group"]["unreached_advisories"] += 1

    ecosystem_rows = []
    total_unreached = 0
    total_links = 0

    for group in groups.values():
        total_unreached += group["unreached_advisories"]
        total_links += group["link_count"]

        confidences = group["confidences"]
        mean_confidence = (
            sum(confidences) / len(confidences) if confidences else None
        )
        by_match_basis = [
            {"basis": basis, "count": count}
            for basis, count in sorted(group["bases"].items())
        ]

        ecosystem_rows.append(
            {
                "ecosystem": group["ecosystem"],
                "feed": group["feed"],
                "advisory_count": group["advisory_count"],
                "unreached_advisories": group["unreached_advisories"],
                "no_alias_advisories": group["no_alias_advisories"],
                "link_count": group["link_count"],
                "distinct_servers": len(group["servers"]),
                "mean_match_confidence": mean_confidence,
                "by_match_basis": by_match_basis,
            }
        )

    ecosystem_rows.sort(key=lambda row: (row["feed"], row["ecosystem"]))

    return {
        "ecosystems": ecosystem_rows,
        "totals": {
            "advisories": len(advisory_rows),
            "unreached": total_unreached,
            "links": total_links,
        },
    }


def _fetch_rows(session: Session) -> tuple[list[dict], list[dict]]:
    advisory_result = session.execute(
        text(
            """
            SELECT id, feed, package, ecosystem, aliases, identities,
                   affected_ranges
            FROM vuln_advisories
            """
        )
    )
    advisories = [dict(row) for row in advisory_result.mappings().all()]

    link_result = session.execute(
        text(
            """
            SELECT advisory_id, server_id, match_basis, match_confidence
            FROM vuln_links
            """
        )
    )
    links = [dict(row) for row in link_result.mappings().all()]
    return advisories, links


@router.get(
    "/vuln/alias-reachability",
    response_model=AliasReachabilityResponse,
    summary="Report vulnerability advisory alias reachability",
)
def get_alias_reachability(
    session: Session = Depends(get_session),
) -> AliasReachabilityResponse:
    advisories, links = _fetch_rows(session)
    return AliasReachabilityResponse(**summarize_reachability(advisories, links))


if __name__ == "__main__":
    fixture_advisories = [
        {
            "id": 1,
            "feed": "osv",
            "ecosystem": "PyPI",
            "aliases": ["CVE-2024-0001"],
        },
        {
            "id": 2,
            "feed": "osv",
            "ecosystem": "PyPI",
            "aliases": [],
        },
    ]
    fixture_links = [
        {
            "advisory_id": 1,
            "server_id": 10,
            "match_basis": "alias",
            "match_confidence": 0.9,
        },
        {
            "advisory_id": 1,
            "server_id": 10,
            "match_basis": "alias",
            "match_confidence": 0.5,
        },
    ]

    report = summarize_reachability(fixture_advisories, fixture_links)
    ecosystem = report["ecosystems"][0]
    assert ecosystem["unreached_advisories"] == 1
    assert ecosystem["no_alias_advisories"] == 1
    assert abs(ecosystem["mean_match_confidence"] - 0.7) < 1e-6
    print("PASS")