"""perspective_admin_api.py -- admin CRUD for saved Perspectives.

Admin builds the taxonomy, everyone navigates it (FATHER's ruling: the
deterministic, governable discovery surface). Mutations are ADMIN-ONLY
(require_admin, the same Clerk-backed gate the verdict surface uses) and every
mutation emits a structured audit log line. Reads are open to any
authenticated principal. Filters are validated against the LIVE facet enums
at write time, so a perspective can never reference a facet or value the
corpus doesn't have.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from facet_enum_service import compute_facets
from perspective_model import (create_perspective, delete_perspective,
                               get_perspective, list_perspectives,
                               update_perspective, validate_facet_filters)
from verdict_breakdown_api import Principal, get_principal, require_admin

router = APIRouter(prefix="/api", tags=["perspectives"])
audit = logging.getLogger("perspective_audit")


class PerspectiveIn(BaseModel):
    name: str
    facet_filters: dict
    description: str = ""


class PerspectiveUpdate(BaseModel):
    name: Optional[str] = None
    facet_filters: Optional[dict] = None
    description: Optional[str] = None


def _serialize(p) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description,
            "facet_filters": p.facet_filters, "created_by": p.created_by,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None}


def _validate_or_400(db: Session, filters: dict) -> None:
    ok, why = validate_facet_filters(filters, compute_facets(db))
    if not ok:
        raise HTTPException(status_code=400, detail=why)


@router.get("/perspectives")
def list_all(db: Session = Depends(get_session),
             principal: Principal = Depends(get_principal)) -> dict:
    return {"perspectives": [_serialize(p) for p in list_perspectives(db)]}


@router.get("/perspectives/{perspective_id}")
def get_one(perspective_id: str, db: Session = Depends(get_session),
            principal: Principal = Depends(get_principal)) -> dict:
    p = get_perspective(db, perspective_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return _serialize(p)


@router.post("/perspectives", status_code=201)
def create(body: PerspectiveIn, db: Session = Depends(get_session),
           principal: Principal = Depends(require_admin)) -> dict:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name required")
    _validate_or_400(db, body.facet_filters)
    p = create_perspective(db, body.name.strip(), body.facet_filters,
                           created_by=principal.user_id,
                           description=body.description)
    audit.info("perspective_create id=%s by=%s name=%r", p.id,
               principal.user_id, p.name)
    return _serialize(p)


@router.put("/perspectives/{perspective_id}")
def update(perspective_id: str, body: PerspectiveUpdate,
           db: Session = Depends(get_session),
           principal: Principal = Depends(require_admin)) -> dict:
    if body.facet_filters is not None:
        _validate_or_400(db, body.facet_filters)
    p = update_perspective(db, perspective_id, name=body.name,
                           description=body.description,
                           facet_filters=body.facet_filters)
    if p is None:
        raise HTTPException(status_code=404, detail="Perspective not found")
    audit.info("perspective_update id=%s by=%s", perspective_id, principal.user_id)
    return _serialize(p)


@router.delete("/perspectives/{perspective_id}", status_code=204)
def remove(perspective_id: str, db: Session = Depends(get_session),
           principal: Principal = Depends(require_admin)) -> None:
    if not delete_perspective(db, perspective_id):
        raise HTTPException(status_code=404, detail="Perspective not found")
    audit.info("perspective_delete id=%s by=%s", perspective_id, principal.user_id)


if __name__ == "__main__":
    # Self-test the RBAC seam + round-trip via the app, with auth overridden
    # the same way the app's own tests do (dependency_overrides).
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import McpServerRegistry as R

    from sqlalchemy.pool import StaticPool
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    SL = sessionmaker(bind=eng)
    shared = SL()
    shared.add(R(server_id="s1", risk_tier="HIGH", verdict="HIGH",
                 registry_source="github", trust_score=10.0))
    shared.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: shared
    who = {"role": "public"}
    test_app.dependency_overrides[get_principal] = (
        lambda: Principal(user_id="u1", role=who["role"]))

    c = TestClient(test_app)
    body = {"name": "High risk", "facet_filters": {"risk_tier": ["HIGH"]}}
    # member (non-admin) create -> 403: require_admin still runs get_principal
    # override through the chain.
    r = c.post("/api/perspectives", json=body)
    assert r.status_code == 403, r.text
    who["role"] = "admin"
    r = c.post("/api/perspectives", json=body)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = c.post("/api/perspectives", json={"name": "bad",
               "facet_filters": {"nope": ["x"]}})
    assert r.status_code == 400
    who["role"] = "public"
    r = c.get("/api/perspectives")
    assert r.status_code == 200
    got = [p for p in r.json()["perspectives"] if p["id"] == pid][0]
    assert got["facet_filters"] == {"risk_tier": ["HIGH"]}
    print("PASS")
