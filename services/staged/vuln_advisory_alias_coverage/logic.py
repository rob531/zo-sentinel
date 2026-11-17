"""Logic for advisory alias coverage."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


_ALIAS_BASES = {
    "alias",
    "aliases",
    "advisory_alias",
    "advisory_aliases",
    "identifier",
    "identifiers",
}


def _value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _parse_aliases(raw: Any) -> List[str]:
    if raw is None:
        return []

    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            decoded = raw
        if isinstance(decoded, list):
            raw = decoded
        elif isinstance(decoded, str):
            raw = [decoded]
        else:
            raw = []

    if not isinstance(raw, (list, tuple, set)):
        return []

    aliases: List[str] = []
    for item in raw:
        if isinstance(item, str):
            alias = item.strip()
            if alias:
                aliases.append(alias)
        elif item is not None:
            alias = str(item).strip()
            if alias:
                aliases.append(alias)
    return aliases


def _is_alias_link(link: Any) -> bool:
    basis = str(_value(link, "match_basis", "") or "").strip().lower()
    return basis in _ALIAS_BASES or basis.startswith("alias")


def _link_matches_alias(link: Any, aliases: Iterable[str]) -> bool:
    if not _is_alias_link(link):
        return False

    alias_set = set(aliases)
    match_value = _value(link, "match_value", None)
    if match_value is None:
        match_value = _value(link, "alias", None)

    if match_value is not None:
        return str(match_value).strip() in alias_set

    basis = str(_value(link, "match_basis", "") or "").lower()
    return any(alias.lower() in basis for alias in aliases)


def compute_alias_coverage(
    advisories: list[dict], links: list[dict]
) -> Dict[str, Any]:
    """Compute alias coverage for advisory and link rows."""
    advisory_count = len(advisories)
    advisories_with_aliases = 0
    alias_total = 0
    alias_linked = 0
    unmatched: List[Dict[str, str]] = []

    links_by_advisory: Dict[str, List[Any]] = {}
    for link in links:
        advisory_id = str(_value(link, "advisory_id", "") or "")
        links_by_advisory.setdefault(advisory_id, []).append(link)

    for advisory in advisories:
        advisory_id = str(_value(advisory, "id", "") or "")
        aliases = _parse_aliases(_value(advisory, "aliases", None))
        if aliases:
            advisories_with_aliases += 1
            alias_total += len(aliases)

        advisory_links = links_by_advisory.get(advisory_id, [])
        for alias in aliases:
            if any(_link_matches_alias(link, aliases) for link in advisory_links):
                alias_linked += 1
            else:
                unmatched.append({"advisory_id": advisory_id, "alias": alias})

    unmatched.sort(key=lambda item: (item["advisory_id"], item["alias"]))

    coverage_ratio = round(alias_linked / alias_total, 4) if alias_total else 0.0
    return {
        "advisory_count": advisory_count,
        "advisories_with_aliases": advisories_with_aliases,
        "alias_total": alias_total,
        "alias_linked": alias_linked,
        "coverage_ratio": coverage_ratio,
        "top_unmatched": unmatched[:25],
    }


def fetch_alias_coverage(db: Session) -> Dict[str, Any]:
    """Read the real advisory and link tables and compute coverage."""
    advisory_rows = db.execute(select(VulnAdvisory)).mappings().all()
    link_rows = db.execute(select(VulnLink)).mappings().all()

    advisories = [
        {
            "id": row.get("id"),
            "feed": row.get("feed"),
            "severity": row.get("severity"),
            "ecosystem": row.get("ecosystem"),
            "package": row.get("package"),
            "aliases": row.get("aliases"),
            "published_at": row.get("published_at"),
        }
        for row in advisory_rows
    ]
    links = [
        {
            "id": row.get("id"),
            "advisory_id": row.get("advisory_id"),
            "server_id": row.get("server_id"),
            "match_basis": row.get("match_basis"),
            "match_confidence": row.get("match_confidence"),
            "match_value": row.get("match_value"),
        }
        for row in link_rows
    ]
    return compute_alias_coverage(advisories, links)


def get_alias_coverage(db: Session) -> Dict[str, Any]:
    """Compatibility name for callers that load coverage from a session."""
    return fetch_alias_coverage(db)


if __name__ == "__main__":
    _coverage = compute_alias_coverage(
        [
            {"id": "a1", "aliases": '["CVE-2024-0001", "GHSA-xxxx-yyyy-zzzz"]'},
            {"id": "a2", "aliases": ["CVE-2024-0002"]},
            {"id": "a3", "aliases": None},
        ],
        [
            {
                "id": "l1",
                "advisory_id": "a1",
                "match_basis": "alias",
                "match_value": "CVE-2024-0001",
            },
            {
                "id": "l2",
                "advisory_id": "a1",
                "match_basis": "alias",
                "match_value": "GHSA-xxxx-yyyy-zzzz",
            },
        ],
    )
    assert _coverage["advisory_count"] == 3
    assert _coverage["alias_total"] == 3
    assert _coverage["alias_linked"] == 2
    assert _coverage["coverage_ratio"] == 0.6667
    print("PASS")