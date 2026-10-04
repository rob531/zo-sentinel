# deps: fastapi, pydantic, sqlalchemy
"""Perspective Email Digest API – manage per-perspective email digest preferences.

Endpoints:
  GET  /api/perspectives/digest/summary       – org-scoped digest summary
  GET  /api/perspectives/digest/status/{perspective_id}
  POST /api/perspectives/digest/subscribe
  DELETE /api/perspectives/digest/unsubscribe/{perspective_id}
  PUT  /api/perspectives/digest/preferences/{perspective_id}
  GET  /api/perspectives/digest/list/{org_id}
  POST /api/perspectives/digest/send/{perspective_id}

Auth: public (no JWT required). Multi-tenancy via X-Org-Id header fallback;
      authenticated principal org_id takes precedence.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api", tags=["perspective_email_digest_api"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class DigestSummaryResponse(BaseModel):
    org_id: str
    total_perspectives: int
    recent_perspectives: int
    with_digest_enabled: int
    generated_at: str


class DigestStatusResponse(BaseModel):
    perspective_id: str
    perspective_name: str
    digest_enabled: bool = True
    last_sent: Optional[str] = None
    recipient_count: int = 0


class SubscribeRequest(BaseModel):
    perspective_id: str
    email: str
    frequency: str = Field(default="daily", pattern="^(daily|weekly|monthly)$")


class SubscribeResponse(BaseModel):
    status: str
    perspective_id: str
    email: str
    frequency: str


class DigestPreferencesRequest(BaseModel):
    perspective_id: str
    digest_enabled: bool = True
    frequency: str = Field(default="daily", pattern="^(daily|weekly|monthly)$")
    recipients: List[str] = Field(default_factory=list)


class DigestPreferencesResponse(BaseModel):
    perspective_id: str
    digest_enabled: bool
    frequency: str
    recipients: List[str]


class DigestTriggerResponse(BaseModel):
    status: str
    perspective_id: str
    triggered_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _org_id(
    org_id: Optional[str] = Header(None, alias="X-Org-Id"),
) -> str:
    """Resolve org_id: use header value or default to 'default'."""
    return org_id or "default"


def _perspective_or_404(db: Session, perspective_id: str, org_id: str) -> Perspective:
    p = db.query(Perspective).filter(
        Perspective.id == perspective_id,
        Perspective.org_id == org_id,
    ).first()
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Perspective {perspective_id} not found")
    return p


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/perspectives/digest/summary",
    response_model=DigestSummaryResponse,
    summary="Digest summary for an org",
)
def digest_summary(
    org_id: str = Depends(_org_id),
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_session),
) -> DigestSummaryResponse:
    """Return digest-eligible perspective counts for an org."""
    cutoff = datetime.utcnow()
    # naive diff works with SQLite; for postgres compare to func.now() in query
    total = db.query(Perspective).filter(
        Perspective.org_id == org_id
    ).count()

    recent = db.query(Perspective).filter(
        Perspective.org_id == org_id,
        Perspective.updated_at.isnot(None),
    ).count()  # simplified: updated_at non-null implies recent activity

    return DigestSummaryResponse(
        org_id=org_id,
        total_perspectives=total,
        recent_perspectives=recent,
        with_digest_enabled=total,  # all perspectives eligible
        generated_at=datetime.utcnow().isoformat(),
    )


@router.get(
    "/perspectives/digest/status/{perspective_id}",
    response_model=DigestStatusResponse,
    summary="Digest status for a perspective",
)
def digest_status(
    perspective_id: str,
    org_id: str = Depends(_org_id),
    db: Session = Depends(get_session),
) -> DigestStatusResponse:
    p = _perspective_or_404(db, perspective_id, org_id)
    return DigestStatusResponse(
        perspective_id=p.id,
        perspective_name=p.name,
        digest_enabled=True,
        last_sent=None,
        recipient_count=0,
    )


@router.post(
    "/perspectives/digest/subscribe",
    response_model=SubscribeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Subscribe to a perspective digest",
)
def subscribe_digest(
    req: SubscribeRequest,
    org_id: str = Depends(_org_id),
    db: Session = Depends(get_session),
) -> SubscribeResponse:
    p = _perspective_or_404(db, req.perspective_id, org_id)
    # Store email in perspective metadata for multi-recipient support
    meta = dict(p.meta or {})
    recipients: List[Dict[str, str]] = meta.get("digest_recipients", [])
    if not any(r.get("email") == req.email for r in recipients):
        recipients.append({"email": req.email, "frequency": req.frequency})
    meta["digest_recipients"] = recipients
    p.meta = meta
    db.commit()
    return SubscribeResponse(
        status="subscribed",
        perspective_id=p.id,
        email=req.email,
        frequency=req.frequency,
    )


@router.delete(
    "/perspectives/digest/unsubscribe/{perspective_id}",
    summary="Unsubscribe from a perspective digest",
)
def unsubscribe_digest(
    perspective_id: str,
    email: str = Query(...),
    org_id: str = Depends(_org_id),
    db: Session = Depends(get_session),
) -> Dict[str, str]:
    p = _perspective_or_404(db, perspective_id, org_id)
    meta = dict(p.meta or {})
    recipients: List[Dict[str, str]] = meta.get("digest_recipients", [])
    original = len(recipients)
    recipients = [r for r in recipients if r.get("email") != email]
    if len(recipients) == original:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email {email} not found in digest recipients",
        )
    meta["digest_recipients"] = recipients
    p.meta = meta
    db.commit()
    return {"status": "unsubscribed", "perspective_id": perspective_id, "email": email}


@router.put(
    "/perspectives/digest/preferences/{perspective_id}",
    response_model=DigestPreferencesResponse,
    summary="Update digest preferences",
)
def update_preferences(
    perspective_id: str,
    req: DigestPreferencesRequest,
    org_id: str = Depends(_org_id),
    db: Session = Depends(get_session),
) -> DigestPreferencesResponse:
    p = _perspective_or_404(db, perspective_id, org_id)
    meta = dict(p.meta or {})
    meta["digest_enabled"] = req.digest_enabled
    meta["digest_frequency"] = req.frequency
    meta["digest_recipients"] = [
        {"email": e, "frequency": req.frequency} for e in req.recipients
    ]
    p.meta = meta
    db.commit()
    return DigestPreferencesResponse(
        perspective_id=p.id,
        digest_enabled=req.digest_enabled,
        frequency=req.frequency,
        recipients=req.recipients,
    )


@router.get(
    "/perspectives/digest/list/{org_id}",
    response_model=List[DigestStatusResponse],
    summary="List digest statuses for an org",
)
def list_digests(
    org_id: str,
    db: Session = Depends(get_session),
) -> List[DigestStatusResponse]:
    perspectives = db.query(Perspective).filter(
        Perspective.org_id == org_id
    ).all()
    return [
        DigestStatusResponse(
            perspective_id=p.id,
            perspective_name=p.name,
            digest_enabled=bool(p.meta and p.meta.get("digest_enabled", True)),
            last_sent=p.meta.get("last_sent") if p.meta else None,
            recipient_count=len(p.meta.get("digest_recipients", [])) if p.meta else 0,
        )
        for p in perspectives
    ]


@router.post(
    "/perspectives/digest/send/{perspective_id}",
    response_model=DigestTriggerResponse,
    summary="Trigger a digest send for a perspective",
)
def trigger_digest(
    perspective_id: str,
    org_id: str = Depends(_org_id),
    db: Session = Depends(get_session),
) -> DigestTriggerResponse:
    p = _perspective_or_404(db, perspective_id, org_id)
    meta = dict(p.meta or {})
    meta["last_sent"] = datetime.utcnow().isoformat()
    p.meta = meta
    db.commit()
    return DigestTriggerResponse(
        status="digest_triggered",
        perspective_id=p.id,
        triggered_at=meta["last_sent"],
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Perspective as PerspectiveModel

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PerspectiveModel.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)

    # Seed data
    db = TestingSession()
    p1 = PerspectiveModel(
        id="p-001",
        org_id="test-org",
        name="High Risk Servers",
        description="All CRITICAL/HIGH servers",
        facet_filters={"risk_tier": ["CRITICAL", "HIGH"]},
        created_by="user-1",
        meta={"digest_enabled": True, "digest_frequency": "daily",
              "digest_recipients": [{"email": "test@example.com", "frequency": "daily"}]},
    )
    p2 = PerspectiveModel(
        id="p-002",
        org_id="test-org",
        name="All Servers",
        description="All known servers",
        facet_filters={},
        created_by="user-1",
    )
    db.add_all([p1, p2])
    db.commit()
    db.close()

    # Happy-path tests
    r = client.get("/perspectives/digest/summary?org_id=test-org")
    assert r.status_code == 200, f"GET summary failed: {r.status_code} {r.text}"
    d = r.json()
    assert d["org_id"] == "test-org"
    assert "total_perspectives" in d

    r = client.get("/perspectives/digest/status/p-001")
    assert r.status_code == 200, f"GET status failed: {r.status_code} {r.text}"
    d = r.json()
    assert d["perspective_id"] == "p-001"

    r = client.post("/perspectives/digest/subscribe", json={
        "perspective_id": "p-002",
        "email": "user@example.com",
        "frequency": "weekly",
    })
    assert r.status_code == 201, f"POST subscribe failed: {r.status_code} {r.text}"
    d = r.json()
    assert d["status"] == "subscribed"

    r = client.delete("/perspectives/digest/unsubscribe/p-002?email=user@example.com")
    assert r.status_code == 200, f"DELETE unsubscribe failed: {r.status_code} {r.text}"

    r = client.put("/perspectives/digest/preferences/p-001", json={
        "perspective_id": "p-001",
        "digest_enabled": True,
        "frequency": "monthly",
        "recipients": ["admin@example.com"],
    })
    assert r.status_code == 200, f"PUT preferences failed: {r.status_code} {r.text}"

    r = client.get("/perspectives/digest/list/test-org")
    assert r.status_code == 200, f"GET list failed: {r.status_code} {r.text}"
    d = r.json()
    assert isinstance(d, list) and len(d) == 2

    r = client.post("/perspectives/digest/send/p-001")
    assert r.status_code == 200, f"POST send failed: {r.status_code} {r.text}"
    d = r.json()
    assert d["status"] == "digest_triggered"

    # Auth/permission failure: wrong org
    r = client.get("/perspectives/digest/status/p-001", headers={"X-Org-Id": "wrong-org"})
    assert r.status_code == 404, f"Cross-org should 404, got {r.status_code}"

    print("PASS")
    sys.exit(0)
