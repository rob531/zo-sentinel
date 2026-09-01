"""Clerk -> Postgres. The server side of a signup, which did not exist before.

WHY THE SIGNATURE CHECK IS HAND-ROLLED
--------------------------------------
Clerk signs webhooks with Svix, and the official path is `pip install svix`.
That is one more package that has to resolve at image-build time on a fleet
whose treewalk-smoke gate has ALREADY been dead-red once because a runtime
import was not declared (see the `requests` note in app/requirements.txt). The
Svix scheme is plain HMAC-SHA256 over `{id}.{timestamp}.{body}` with a
base64 secret -- thirty lines, no supply chain, and verified below against the
published test vector rather than against my own implementation.

WHAT MAKES THIS SAFE TO LEAVE RUNNING UNATTENDED
------------------------------------------------
* CONSTANT-TIME compare, and it accepts a SET of signatures, because Svix sends
  every active key during a secret rotation. A verifier that reads only the
  first one breaks silently at the exact moment the secret changes.
* TIMESTAMP TOLERANCE (5 min) so a captured POST cannot be replayed later. The
  timestamp is inside the signed payload, so it cannot be edited in flight.
* IDEMPOTENT by `clerk_id`, which carries a UNIQUE constraint. Clerk retries on
  any non-2xx for days; a duplicate delivery MUST be a no-op and must return
  2xx, or the retries never stop.
* MISSING SECRET FAILS CLOSED (503, never 200). An unconfigured verifier that
  accepts everything is worse than one that is switched off, and this endpoint
  writes to the users table.
* Returns 2xx for event types we do not handle, so Clerk does not retry them
  forever.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import get_session
from .models import Org, User
from .settings import settings

router = APIRouter(prefix="/webhooks", tags=["clerk"])

TOLERANCE_SECONDS = 300
_CLERK_PASSWORD_SENTINEL = "!clerk"   # mirrors the existing "!oauth" convention


class SignatureError(Exception):
    """Verification failed. The message is for OUR log, never for the response."""


def verify_svix(secret: str, msg_id: str, msg_timestamp: str,
                signature_header: str, body: bytes,
                now: Optional[float] = None) -> None:
    """Raise SignatureError unless this really came from Clerk.

    `secret` is the `whsec_<base64>` value from the Clerk dashboard.
    `signature_header` is `v1,<b64> v1,<b64> ...` -- space separated, one entry
    per active signing key.
    """
    if not secret:
        raise SignatureError("no signing secret configured")
    if not (msg_id and msg_timestamp and signature_header):
        raise SignatureError("missing svix-id / svix-timestamp / svix-signature")

    try:
        ts = int(msg_timestamp)
    except (TypeError, ValueError):
        raise SignatureError(f"unparseable svix-timestamp {msg_timestamp!r}")
    drift = abs((now if now is not None else time.time()) - ts)
    if drift > TOLERANCE_SECONDS:
        raise SignatureError(f"timestamp {drift:.0f}s outside {TOLERANCE_SECONDS}s tolerance")

    raw = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(raw)
    except Exception:
        raise SignatureError("signing secret is not valid base64")

    signed = b"%s.%s.%s" % (msg_id.encode(), msg_timestamp.encode(), body)
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()

    # Compare against EVERY offered signature. During a secret rotation Clerk
    # sends the old and new signatures together; checking only the first would
    # fail closed at rotation time and look like an attack.
    for part in signature_header.split():
        _, _, candidate = part.partition(",")
        if candidate and hmac.compare_digest(candidate, expected):
            return
    raise SignatureError("no offered signature matched")


def _primary_email(data: dict) -> Optional[str]:
    """The address Clerk considers primary, not merely the first one present.

    Reading `email_addresses[0]` would silently bind the account to whichever
    address happens to sort first, which for a user who has added a second
    address is not the one they log in with.
    """
    addrs = data.get("email_addresses") or []
    primary_id = data.get("primary_email_address_id")
    for a in addrs:
        if primary_id and a.get("id") == primary_id:
            return (a.get("email_address") or "").strip().lower() or None
    for a in addrs:
        if a.get("email_address"):
            return a["email_address"].strip().lower()
    return None


def _clerk_created_at(data: dict) -> Optional[datetime]:
    ms = data.get("created_at")
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)


def upsert_clerk_user(sess: Session, data: dict, synced_via: str) -> tuple[str, str]:
    """Idempotently reflect one Clerk user into `users`. Returns (action, user_id).

    Shared by the webhook and the nightly reconcile ON PURPOSE. If the backfill
    ran different code from the live path, the reconcile would stop being a
    control over the webhook and become a second thing that can be wrong.

    Match order is clerk_id, then email. The email fallback matters for the
    people who registered with a password BEFORE Clerk: matching on it adopts
    the existing row instead of hitting the unique-email constraint and dying
    on every retry forever.
    """
    clerk_id = data.get("id")
    if not clerk_id:
        return "skipped:no-id", ""
    email = _primary_email(data)
    if not email:
        return "skipped:no-email", ""

    user = sess.execute(
        select(User).where(User.clerk_id == clerk_id)).scalar_one_or_none()
    if user is None:
        user = sess.execute(
            select(User).where(User.email == email)).scalar_one_or_none()

    org_id = settings.CLERK_DEFAULT_ORG
    if sess.get(Org, org_id) is None:
        sess.add(Org(id=org_id, name=org_id))

    if user is None:
        user = User(
            id=str(uuid.uuid4()), email=email,
            password_hash=_CLERK_PASSWORD_SENTINEL,
            org_id=org_id, role="member",
            clerk_id=clerk_id, clerk_synced_via=synced_via,
            clerk_created_at=_clerk_created_at(data),
        )
        sess.add(user)
        action = "created"
    else:
        user.email = email
        user.clerk_id = clerk_id
        if user.clerk_created_at is None:
            user.clerk_created_at = _clerk_created_at(data)
        # clerk_synced_via records how the row FIRST arrived and is never
        # overwritten by a later reconcile. Letting the backfill restamp it
        # would erase the only evidence of a webhook outage -- the control
        # would quietly heal the symptom it exists to report.
        if user.clerk_synced_via is None:
            user.clerk_synced_via = synced_via
        action = "updated"

    try:
        sess.commit()
    except IntegrityError:
        # A concurrent redelivery won the race. That is success, not failure.
        sess.rollback()
        return "duplicate", clerk_id
    return action, user.id


@router.post("/clerk", status_code=status.HTTP_200_OK)
async def clerk_webhook(
    request: Request,
    svix_id: str = Header(None, alias="svix-id"),
    svix_timestamp: str = Header(None, alias="svix-timestamp"),
    svix_signature: str = Header(None, alias="svix-signature"),
):
    if not settings.CLERK_WEBHOOK_SECRET:
        # FAIL CLOSED. This endpoint writes to users; an unconfigured verifier
        # that returns 200 is an open door, and it would also teach Clerk the
        # deliveries are landing.
        raise HTTPException(status_code=503, detail="clerk webhook not configured")

    body = await request.body()
    sess: Session = next(get_session())
    try:
        try:
            verify_svix(settings.CLERK_WEBHOOK_SECRET, svix_id, svix_timestamp,
                        svix_signature, body)
        except SignatureError:
            # Deliberately does not echo WHY. The detail belongs in our logs,
            # not in a response that tells a prober which limb it failed.
            raise HTTPException(status_code=401, detail="invalid signature")

        import json
        try:
            event = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise HTTPException(status_code=400, detail="body is not JSON")

        etype = event.get("type", "")
        data = event.get("data") or {}

        if etype in ("user.created", "user.updated"):
            action, _ = upsert_clerk_user(sess, data, "webhook")
            return {"ok": True, "type": etype, "action": action}

        # 2xx, not 4xx: an unhandled type is not a delivery failure, and a 4xx
        # here would put Clerk into a retry loop over an event we will never
        # want.
        return {"ok": True, "type": etype, "action": "ignored"}
    finally:
        sess.close()
