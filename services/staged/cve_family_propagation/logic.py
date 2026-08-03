"""
services/staged/cve_family_propagation/logic.py

Logic for propagating threat associations across CVE families.
"""

from typing import List, Dict, Any

from fastapi import Depends
from sqlalchemy.orm import Session
from sqlalchemy import or_, inspect

from app.db import get_session, Base
from app.models import VulnAdvisory, VulnLink, McpThreatAssociation  # type: ignore


def _discover_column(model, candidates: List[str]):
    """Return the first column on *model* whose name matches one of *candidates*."""
    for col in model.__table__.c:
        if col.key in candidates:
            return col
    raise ValueError(f"No column matching {candidates} on {model.__name__}")


# --------------------------------------------------------------------------- #
# Core propagation function
# --------------------------------------------------------------------------- #
def propagate_family(
    advisory_ids: List[str],
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """
    Propagate threat associations for the supplied advisory IDs.

    Returns a dict:
        {
            "propagated": int,   # number of new threat association rows created
            "errors": List[str]  # any errors encountered
        }
    """
    errors: List[str] = []
    propagated = 0

    # ------------------------------------------------------------------- #
    # Resolve columns dynamically (protects against schema changes)
    # ------------------------------------------------------------------- #
    try:
        adv_col = _discover_column(VulnAdvisory, ["advisory_id", "advisory", "name", "identifier"])
        cve_col = _discover_column(VulnAdvisory, ["cve_id", "cve", "cve_identifier"])
        link_cve_cols = [
            col for col in VulnLink.__table__.c if "cve" in col.key
        ]
        if len(link_cve_cols) < 2:
            raise ValueError("VulnLink must have at least two CVE columns")
        link_cve_a, link_cve_b = link_cve_cols[:2]

        assoc_cve_col = _discover_column(McpThreatAssociation, ["cve_id", "cve", "cve_identifier"])
        assoc_adv_col = _discover_column(
            McpThreatAssociation,
            ["advisory_id", "advisory", "source_advisory", "identifier"],
        )
    except Exception as exc:  # pragma: no cover
        return {"propagated": 0, "errors": [str(exc)]}

    # ------------------------------------------------------------------- #
    # 1. Load advisories
    # ------------------------------------------------------------------- #
    advisories = (
        db.query(VulnAdvisory)
        .filter(adv_col.in_(advisory_ids))
        .all()
    )
    if not advisories:
        errors.append("No advisories found for supplied IDs")
        return {"propagated": 0, "errors": errors}

    # ------------------------------------------------------------------- #
    # 2. Determine the CVE family
    # ------------------------------------------------------------------- #
    original_cves = {getattr(a, cve_col.key) for a in advisories}
    family_cves = set(original_cves)

    # fetch linked CVEs
    links = (
        db.query(VulnLink)
        .filter(
            or_(
                link_cve_a.in_(original_cves),
                link_cve_b.in_(original_cves),
            )
        )
        .all()
    )
    for link in links:
        family_cves.add(getattr(link, link_cve_a.key))
        family_cves.add(getattr(link, link_cve_b.key))

    # ------------------------------------------------------------------- #
    # 3. Propagate threat associations
    # ------------------------------------------------------------------- #
    # Existing associations for this advisory set
    existing = {
        (getattr(a, assoc_cve_col.key), getattr(a, assoc_adv_col.key))
        for a in db.query(McpThreatAssociation)
        .filter(assoc_adv_col.in_(advisory_ids))
        .all()
    }

    new_assocs = []
    for adv_id in advisory_ids:
        for cve in family_cves:
            key = (cve, adv_id)
            if key in existing:
                continue
            assoc_kwargs = {
                assoc_cve_col.key: cve,
                assoc_adv_col.key: adv_id,
                "source": "family_propagation",
            }
            new_assocs.append(McpThreatAssociation(**assoc_kwargs))

    if new_assocs:
        db.add_all(new_assocs)
        db.commit()
        propagated = len(new_assocs)

    return {"propagated": propagated, "errors": errors}


# --------------------------------------------------------------------------- #
# Self‑test (executed when running the module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and initialise schema
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    test_db = SessionLocal()

    # ------------------------------------------------------------------- #
    # Helper to discover columns (re‑use the same logic as above)
    # ------------------------------------------------------------------- #
    adv_col = _discover_column(VulnAdvisory, ["advisory_id", "advisory", "name", "identifier"])
    cve_col = _discover_column(VulnAdvisory, ["cve_id", "cve", "cve_identifier"])
    link_cve_cols = [col for col in VulnLink.__table__.c if "cve" in col.key]
    link_cve_a, link_cve_b = link_cve_cols[:2]

    # ------------------------------------------------------------------- #
    # Seed test data:
    #   ADV1 -> CVE-1
    #   ADV2 -> CVE-2
    #   ADV3 -> CVE-3
    #   Links: CVE-1 <-> CVE-2, CVE-2 <-> CVE-3
    # ------------------------------------------------------------------- #
    adv_data = [
        ("ADV1", "CVE-1"),
        ("ADV2", "CVE-2"),
        ("ADV3", "CVE-3"),
    ]
    for adv_id, cve in adv_data:
        adv_kwargs = {adv_col.key: adv_id, cve_col.key: cve}
        test_db.add(VulnAdvisory(**adv_kwargs))

    link_data = [
        ("CVE-1", "CVE-2"),
        ("CVE-2", "CVE-3"),
    ]
    for a, b in link_data:
        link_kwargs = {link_cve_a.key: a, link_cve_b.key: b}
        test_db.add(VulnLink(**link_kwargs))

    test_db.commit()

    # ------------------------------------------------------------------- #
    # Run propagation for ADV1 – expect CVE-2 and CVE-3 to receive
    # propagated associations (total 2 new rows)
    # ------------------------------------------------------------------- #
    result = propagate_family(["ADV1"], db=test_db)
    expected = 2
    if result["propagated"] == expected and not result["errors"]:
        print("PASS")
    else:
        print("FAIL", result)