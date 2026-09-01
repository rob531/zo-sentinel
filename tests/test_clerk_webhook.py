"""End-to-end controls for the Clerk -> Postgres signup path.

Every assertion here is driven BOTH ways where a direction exists. The three
that matter most, because they are the ones that would fail silently in prod:

  * an unsigned or wrongly-signed POST must not write a row,
  * a redelivery must be a no-op AND still return 2xx (or Clerk retries for
    days),
  * an unconfigured secret must fail CLOSED, because this endpoint writes to
    `users`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import clerk_webhook as cw
from app.db import Base, get_session
from app.main import app
from app.models import Org, User
from app.settings import settings

SECRET = "whsec_" + base64.b64encode(b"clerk-test-signing-key-0123456789").decode()


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        s = Sess()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    monkeypatch.setattr(cw, "get_session", _override)
    yield Sess
    app.dependency_overrides.clear()


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "CLERK_WEBHOOK_SECRET", SECRET, raising=False)
    monkeypatch.setattr(settings, "CLERK_DEFAULT_ORG", "public", raising=False)
    return TestClient(app)


def _event(clerk_id="user_abc123", email="alice@example.com",
           etype="user.created", created_ms=None):
    return {
        "type": etype,
        "data": {
            "id": clerk_id,
            "primary_email_address_id": "idem1",
            "email_addresses": [{"id": "idem1", "email_address": email}],
            "created_at": created_ms or int(time.time() * 1000),
        },
    }


def _post(client, event, secret=SECRET, msg_id="msg_1", ts=None, mangle=False):
    body = json.dumps(event).encode()
    ts = str(int(ts if ts is not None else time.time()))
    raw = secret.split("_", 1)[1]
    sig = base64.b64encode(hmac.new(
        base64.b64decode(raw),
        b"%s.%s.%s" % (msg_id.encode(), ts.encode(), body),
        hashlib.sha256).digest()).decode()
    if mangle:
        sig = base64.b64encode(b"x" * 32).decode()
    return client.post("/webhooks/clerk", content=body, headers={
        "svix-id": msg_id, "svix-timestamp": ts, "svix-signature": f"v1,{sig}",
        "content-type": "application/json",
    })


def test_signup_creates_a_user(client, db):
    r = _post(client, _event())
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "created"
    with db() as s:
        u = s.execute(select(User).where(User.clerk_id == "user_abc123")).scalar_one()
        assert u.email == "alice@example.com"
        assert u.clerk_synced_via == "webhook"
        assert u.org_id == "public"
        assert s.get(Org, "public") is not None


def test_redelivery_is_a_noop_and_still_returns_2xx(client, db):
    """Clerk retries on any non-2xx. A duplicate that 4xx'd would retry forever."""
    assert _post(client, _event()).status_code == 200
    r2 = _post(client, _event(), msg_id="msg_2")
    assert r2.status_code == 200
    with db() as s:
        assert len(s.execute(select(User)).scalars().all()) == 1


def test_bad_signature_writes_nothing(client, db):
    r = _post(client, _event(), mangle=True)
    assert r.status_code == 401
    with db() as s:
        assert s.execute(select(User)).scalars().all() == []


def test_replayed_request_is_rejected_on_timestamp(client, db):
    r = _post(client, _event(), ts=time.time() - 3600)
    assert r.status_code == 401
    with db() as s:
        assert s.execute(select(User)).scalars().all() == []


def test_unconfigured_secret_fails_closed(client, db, monkeypatch):
    """503, never 200. An open verifier on a table-writing endpoint is worse
    than no endpoint, and a 200 would also tell Clerk the deliveries landed."""
    monkeypatch.setattr(settings, "CLERK_WEBHOOK_SECRET", "", raising=False)
    r = _post(client, _event(), )
    assert r.status_code == 503
    with db() as s:
        assert s.execute(select(User)).scalars().all() == []


def test_existing_password_user_is_adopted_not_duplicated(client, db):
    """Someone who registered before Clerk must be matched on email, or the
    unique-email constraint kills every redelivery of their signup forever."""
    with db() as s:
        s.add(Org(id="public", name="public"))
        s.add(User(id="legacy-1", email="bob@example.com", password_hash="x",
                   org_id="public", role="admin"))
        s.commit()
    r = _post(client, _event(clerk_id="user_bob", email="bob@example.com"))
    assert r.status_code == 200
    assert r.json()["action"] == "updated"
    with db() as s:
        rows = s.execute(select(User)).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == "legacy-1"
        assert rows[0].clerk_id == "user_bob"
        assert rows[0].role == "admin", "adoption must not downgrade a role"


def test_reconcile_never_overwrites_how_the_row_first_arrived(client, db):
    """The whole outage signal lives in clerk_synced_via. If a later reconcile
    restamped it, the control would heal the symptom it exists to report."""
    assert _post(client, _event()).status_code == 200
    with db() as s:
        cw.upsert_clerk_user(s, _event()["data"], "reconcile")
        u = s.execute(select(User).where(User.clerk_id == "user_abc123")).scalar_one()
        assert u.clerk_synced_via == "webhook"


def test_unhandled_event_type_is_accepted_not_retried(client, db):
    r = _post(client, _event(etype="session.created"))
    assert r.status_code == 200
    assert r.json()["action"] == "ignored"
    with db() as s:
        assert s.execute(select(User)).scalars().all() == []


def test_user_with_no_email_is_skipped_cleanly(client, db):
    ev = _event()
    ev["data"]["email_addresses"] = []
    r = _post(client, ev)
    assert r.status_code == 200
    assert r.json()["action"].startswith("skipped")
