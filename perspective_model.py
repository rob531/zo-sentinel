"""perspective_model.py -- saved-perspective persistence + validation.

v1.1 "Perspectives" (PRODUCT_SPEC Appendix F): a perspective is an admin-built
saved facet filter over the scored registry -- deterministic, governable,
reproducible (FATHER's chosen first differentiator). Rows live in the app DB
(app.models.Perspective), written through the same SQLAlchemy seam every
feature router uses.

facet_filters shape (validated against the live enum universe):
    {"risk_tier": ["HIGH"], "axis:auth_strength": ["WEAK", "UNKNOWN"]}
Unknown facet keys AND unknown values are REJECTED -- a perspective can never
silently reference a facet the corpus doesn't have.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Perspective

VALID_KEY_PREFIX = "axis:"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_facet_filters(filters: dict, enums: Dict[str, List[dict]]) -> Tuple[bool, str]:
    """(ok, reason). `enums` is facet_enum_service.compute_facets() output --
    passed in, so validation is pure and self-tests need no network/DB."""
    if not isinstance(filters, dict) or not filters:
        return False, "facet_filters must be a non-empty object"
    for key, values in filters.items():
        if key not in enums:
            return False, f"unknown facet key: {key!r}"
        if not isinstance(values, list) or not values:
            return False, f"facet {key!r} must map to a non-empty list"
        known = {d["value"] for d in enums[key]}
        for v in values:
            if str(v) not in known:
                return False, f"unknown value {v!r} for facet {key!r}"
    return True, "ok"


def create_perspective(db: Session, name: str, facet_filters: dict,
                       created_by: str, description: str = "",
                       org_id: Optional[str] = None) -> Perspective:
    p = Perspective(id=uuid.uuid4().hex, org_id=org_id, name=name,
                    description=description, facet_filters=facet_filters,
                    created_by=created_by, created_at=_now(), updated_at=_now())
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def get_perspective(db: Session, perspective_id: str) -> Optional[Perspective]:
    return db.get(Perspective, perspective_id)


def list_perspectives(db: Session, org_id: Optional[str] = None) -> List[Perspective]:
    q = select(Perspective).order_by(Perspective.created_at.desc())
    if org_id is not None:
        q = q.where((Perspective.org_id == org_id) | (Perspective.org_id.is_(None)))
    return list(db.execute(q).scalars())


def update_perspective(db: Session, perspective_id: str, *, name=None,
                       description=None, facet_filters=None) -> Optional[Perspective]:
    p = db.get(Perspective, perspective_id)
    if p is None:
        return None
    if name is not None:
        p.name = name
    if description is not None:
        p.description = description
    if facet_filters is not None:
        p.facet_filters = facet_filters
    p.updated_at = _now()
    db.commit()
    db.refresh(p)
    return p


def delete_perspective(db: Session, perspective_id: str) -> bool:
    p = db.get(Perspective, perspective_id)
    if p is None:
        return False
    db.delete(p)
    db.commit()
    return True


if __name__ == "__main__":
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()

    enums = {"risk_tier": [{"value": "HIGH", "count": 2}, {"value": "LOW", "count": 1}],
             "axis:auth_strength": [{"value": "WEAK", "count": 1}]}
    ok, _ = validate_facet_filters({"risk_tier": ["HIGH"]}, enums)
    assert ok
    ok, why = validate_facet_filters({"hosting_model": ["cloud"]}, enums)
    assert not ok and "unknown facet key" in why
    ok, why = validate_facet_filters({"risk_tier": ["EXTREME"]}, enums)
    assert not ok and "unknown value" in why

    p = create_perspective(s, "Weak-auth HIGH", {"risk_tier": ["HIGH"]}, "admin_1")
    assert get_perspective(s, p.id).facet_filters == {"risk_tier": ["HIGH"]}
    assert len(list_perspectives(s)) == 1
    update_perspective(s, p.id, description="watchlist")
    assert get_perspective(s, p.id).description == "watchlist"
    assert delete_perspective(s, p.id) is True and get_perspective(s, p.id) is None
    print("PASS")
