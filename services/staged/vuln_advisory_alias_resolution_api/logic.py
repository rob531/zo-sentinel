import itertools
from collections import defaultdict
from typing import Dict, List, Set, Tuple

from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import VulnAdvisory


def _flatten_aliases(advisory: VulnAdvisory) -> Set[str]:
    """Extract alias strings from ``aliases`` and ``identities`` JSON columns."""
    result: Set[str] = set()
    if advisory.aliases:
        # aliases column is expected to be a list of strings
        result.update(str(a) for a in advisory.aliases if a)
    if advisory.identities:
        # identities column is also expected to be a list of strings
        result.update(str(i) for i in advisory.identities if i)
    return result


def resolve_aliases(min_aliases: int = 2) -> Dict:
    """
    Detect advisory groups that share one or more alias strings.

    Parameters
    ----------
    min_aliases:
        Minimum number of distinct advisories an alias must appear in to be
        considered a fan‑out signal.

    Returns
    -------
    dict
        ``{
            "advisory_count": int,
            "alias_groups": int,
            "groups": [
                {
                    "primary_advisory_id": str,
                    "alias_count": int,
                    "aliases": [str],
                    "sibling_advisory_ids": [str],
                },
                ...
            ],
        }``
    """
    session: Session = get_session()
    # ------------------------------------------------------------------
    # 1. Build advisory -> alias set mapping
    # ------------------------------------------------------------------
    advisory_to_aliases: Dict[str, Set[str]] = {}
    for adv in session.query(VulnAdvisory.id, VulnAdvisory.aliases, VulnAdvisory.identities):
        advisory_to_aliases[adv.id] = _flatten_aliases(adv)

    # ------------------------------------------------------------------
    # 2. Build alias -> advisory set mapping (only keep those meeting the
    #    ``min_aliases`` threshold)
    # ------------------------------------------------------------------
    alias_to_advisories: Dict[str, Set[str]] = defaultdict(set)
    for adv_id, aliases in advisory_to_aliases.items():
        for alias in aliases:
            alias_to_advisories[alias].add(adv_id)

    # Keep only aliases that appear in at least ``min_aliases`` advisories
    filtered_aliases = {
        alias: advs for alias, advs in alias_to_advisories.items() if len(advs) >= min_aliases
    }

    # ------------------------------------------------------------------
    # 3. Group aliases that map to the same advisory set.
    #    Use the frozenset of advisory ids as the grouping key.
    # ------------------------------------------------------------------
    advisory_set_to_aliases: Dict[frozenset, List[str]] = defaultdict(list)
    for alias, adv_set in filtered_aliases.items():
        advisory_set_to_aliases[frozenset(adv_set)].append(alias)

    groups: List[Dict] = []
    all_involved_advisories: Set[str] = set()

    for adv_set_frozen, aliases in advisory_set_to_aliases.items():
        adv_set = set(adv_set_frozen)
        if len(adv_set) < min_aliases:
            continue  # safety net; should already be satisfied
        primary = min(adv_set, key=lambda x: (int(x) if x.isdigit() else x))
        siblings = sorted(a for a in adv_set if a != primary)
        group = {
            "primary_advisory_id": str(primary),
            "alias_count": len(aliases),
            "aliases": sorted(aliases),
            "sibling_advisory_ids": [str(s) for s in siblings],
        }
        groups.append(group)
        all_involved_advisories.update(adv_set)

    # ------------------------------------------------------------------
    # 4. Sort & limit groups
    # ------------------------------------------------------------------
    groups.sort(
        key=lambda g: (-g["alias_count"], g["primary_advisory_id"])
    )
    groups = groups[:200]

    result = {
        "advisory_count": len(all_involved_advisories),
        "alias_groups": len(groups),
        "groups": groups,
    }
    return result


# ----------------------------------------------------------------------
# Self‑test (executed when the module is run directly)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Build an in‑memory SQLite DB and override the session dependency.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, scoped_session

    # ------------------------------------------------------------------
    # 1. Create engine & session factory
    # ------------------------------------------------------------------
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionFactory = sessionmaker(bind=engine)
    scoped = scoped_session(SessionFactory)

    # ------------------------------------------------------------------
    # 2. Create tables
    # ------------------------------------------------------------------
    Base.metadata.create_all(engine)

    # ------------------------------------------------------------------
    # 3. Populate test data
    # ------------------------------------------------------------------
    test_session = scoped()
    adv1 = VulnAdvisory(
        id="1",
        feed="test",
        summary="adv1",
        severity="low",
        ecosystem="pypi",
        package="pkg1",
        affected_ranges=[],
        aliases=["CVE-2024-1", "GHSA-xxxx"],
        source_url="http://example.com/1",
        published_at=None,
        fetched_at=None,
        identities=["CVE-2024-1", "GHSA-xxxx"],
        content_hash="hash1",
    )
    adv2 = VulnAdvisory(
        id="2",
        feed="test",
        summary="adv2",
        severity="medium",
        ecosystem="pypi",
        package="pkg2",
        affected_ranges=[],
        aliases=["CVE-2024-1", "GHSA-xxxx"],
        source_url="http://example.com/2",
        published_at=None,
        fetched_at=None,
        identities=["CVE-2024-1", "GHSA-xxxx"],
        content_hash="hash2",
    )
    adv3 = VulnAdvisory(
        id="3",
        feed="test",
        summary="adv3",
        severity="high",
        ecosystem="pypi",
        package="pkg3",
        affected_ranges=[],
        aliases=["CVE-2025-2"],
        source_url="http://example.com/3",
        published_at=None,
        fetched_at=None,
        identities=["CVE-2025-2"],
        content_hash="hash3",
    )
    test_session.add_all([adv1, adv2, adv3])
    test_session.commit()

    # ------------------------------------------------------------------
    # 4. Override the get_session dependency used by ``resolve_aliases``.
    # ------------------------------------------------------------------
    def _override_get_session() -> Session:  # pragma: no cover
        return test_session

    # Monkey‑patch the imported ``get_session`` used inside this module.
    import types

    get_session = types.FunctionType(
        _override_get_session.__code__,
        globals(),
        name="get_session",
        argdefs=_override_get_session.__defaults__,
        closure=_override_get_session.__closure__,
    )

    # ------------------------------------------------------------------
    # 5. Run the logic and validate expectations.
    # ------------------------------------------------------------------
    result = resolve_aliases(min_aliases=2)

    assert result["alias_groups"] >= 1, "expected at least one alias group"
    assert result["advisory_count"] >= 2, "expected at least two advisories involved"
    # Find a group with at least two aliases and non‑empty siblings.
    matching_groups = [
        g
        for g in result["groups"]
        if g["alias_count"] >= 2 and len(g["sibling_advisory_ids"]) > 0
    ]
    assert matching_groups, "expected a group with >=2 aliases and siblings"
    print("PASS")