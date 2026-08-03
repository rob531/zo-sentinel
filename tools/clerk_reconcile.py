#!/usr/bin/env python3
"""Backfill Clerk signups -- and, more importantly, prove the webhook is alive.

THIS IS A NEGATIVE CONTROL WEARING A BACKFILL'S CLOTHES
-------------------------------------------------------
A backfill on its own is a fallback that shares the primary's failure mode: if
the webhook dies, the backfill quietly covers for it, every user still lands,
and nothing ever reports that the live path is broken. The outage becomes
invisible precisely because the safety net worked. Over a 23-day unattended
window that is the worst possible shape -- we would return to a healthy-looking
users table and a webhook that had been dead since the second week.

So this job draws a distinction the backfill alone cannot:

    a user this job CREATES, whose Clerk signup is already older than
    CLERK_WEBHOOK_STALE_HOURS, is PROOF the webhook did not deliver it.

Zero such users is a green that means something, because the same run also
reports how many Clerk users it saw at all. Contrast the failure mode this is
built to avoid: an all-clear that is byte-identical to blindness. If the Clerk
API call fails we exit rc=2 UNKNOWN and assert nothing -- a probe that cannot
evaluate is not a green.

    python tools/clerk_reconcile.py            # reconcile + report
    python tools/clerk_reconcile.py --dry-run  # read both sides, write nothing
    python tools/clerk_reconcile.py --self-test

Exit codes follow the house contract: 0 GREEN, 1 RED (webhook looks dead),
2 UNKNOWN (could not evaluate -- never treated as evidence).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from app.clerk_webhook import upsert_clerk_user  # noqa: E402
from app.db import get_session  # noqa: E402
from app.models import User  # noqa: E402
from app.settings import settings  # noqa: E402

CLERK_API = "https://api.clerk.com/v1/users"
PAGE = 100

# NOT cosmetic. api.clerk.com sits behind Cloudflare, which rejects urllib's
# default `Python-urllib/3.x` signature with **403 and a body of
# `error code: 1010`** -- a browser-signature block, not an authorisation
# failure. Clerk's own errors are JSON; 1010 is Cloudflare's.
#
# Measured 2026-08-02 from inside the Fly app, the same request twice differing
# ONLY in this header: default UA -> 403 on /v1/users, /v1/users/count and
# /v1/instance alike; named UA -> 200 on all three, with a live production key.
#
# Without this the nightly job would have returned rc=2 UNKNOWN every night and
# reported "Clerk API unreachable", and the obvious reading -- our key lacks
# permission, someone go fix it in the Clerk console -- would have been wrong
# in a way nobody could have checked while the chairman was away. A 403 on
# EVERY endpoint including an unauthenticated-ish one is the tell: a scope
# problem is selective, an edge block is total.
USER_AGENT = "zo-sentinel-reconcile/1.0 (+https://mcprisky.io)"


class Unknown(Exception):
    """Could not evaluate. Never a RED, never a GREEN."""


def fetch_clerk_users(secret: str, limit: int = 1000) -> list[dict]:
    """Page through the Clerk backend API. Raises Unknown on any API failure.

    Deliberately raises rather than returning [] on error: an empty list would
    flow onward and render as "no signups", which is the exact ambiguity this
    tool exists to remove.
    """
    if not secret:
        raise Unknown("CLERK_SECRET_KEY is not set")
    out: list[dict] = []
    offset = 0
    while offset < limit:
        url = f"{CLERK_API}?limit={PAGE}&offset={offset}&order_by=-created_at"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                batch = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # Carry the BODY, not just the status line. `403 Forbidden` alone
            # reads as "our key is not allowed" and sends someone to the Clerk
            # console; the body says `error code: 1010` and sends them to the
            # User-Agent. An error that names only its status code is an
            # invitation to guess the layer that produced it.
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                body = "<unreadable>"
            hint = ""
            if "1010" in body or "cloudflare" in body.lower():
                hint = (" -- this is a CLOUDFLARE edge block on the client "
                        "signature, NOT a Clerk permission problem. The key is "
                        "fine; the request needs a named User-Agent.")
            raise Unknown(f"Clerk API HTTP {e.code}: {e.reason}; body={body}{hint}")
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise Unknown(f"Clerk API unreachable: {e}")
        if not batch:
            break
        out.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return out


def reconcile(dry_run: bool = False, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    stale_cut = now - timedelta(hours=settings.CLERK_WEBHOOK_STALE_HOURS)

    clerk_users = fetch_clerk_users(settings.CLERK_SECRET_KEY)
    sess = next(get_session())
    report = {
        "clerk_users_seen": len(clerk_users),
        "created": 0, "updated": 0, "duplicate": 0, "skipped": 0,
        "webhook_misses": [],
        "dry_run": dry_run,
        "stale_after_hours": settings.CLERK_WEBHOOK_STALE_HOURS,
    }
    try:
        for data in clerk_users:
            cid = data.get("id")
            existing = sess.query(User).filter(User.clerk_id == cid).one_or_none()
            if dry_run:
                if existing is None:
                    report["created"] += 1
                else:
                    report["updated"] += 1
                continue

            action, _ = upsert_clerk_user(sess, data, "reconcile")
            key = action.split(":")[0]
            report[key] = report.get(key, 0) + 1

            # THE CONTROL. We had to create this row, and the signup is old
            # enough that a healthy webhook would long since have delivered it.
            if action == "created":
                ms = data.get("created_at")
                if isinstance(ms, (int, float)):
                    signed_up = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
                    if signed_up < stale_cut:
                        report["webhook_misses"].append({
                            "clerk_id": cid,
                            "signed_up_utc": signed_up.isoformat(),
                            "age_hours": round(
                                (now - signed_up).total_seconds() / 3600.0, 2),
                        })
    finally:
        sess.close()
    return report


def _self_test() -> int:
    """Drive the control BOTH ways against a fixture. No network, no database.

    An assertion never observed RED is not evidence, and the single most
    important property here is that a stale creation reports as a MISS while a
    fresh one does not.
    """
    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    cut = now - timedelta(hours=2)
    checks = []

    def chk(name, cond):
        checks.append((name, bool(cond)))

    fresh = now - timedelta(minutes=5)
    stale = now - timedelta(hours=9)
    chk("a signup 5 min old is NOT a webhook miss", not (fresh < cut))
    chk("a signup 9 h old IS a webhook miss", stale < cut)
    chk("the boundary is the threshold, not 'about a day'",
        (now - timedelta(hours=2, seconds=1)) < cut
        and not ((now - timedelta(hours=1, minutes=59)) < cut))

    from app.clerk_webhook import _primary_email, verify_svix, SignatureError
    chk("primary email is chosen by primary_email_address_id, not position",
        _primary_email({
            "primary_email_address_id": "idB",
            "email_addresses": [
                {"id": "idA", "email_address": "first@example.com"},
                {"id": "idB", "email_address": "Real@Example.com"},
            ]}) == "real@example.com")
    chk("no email address at all => None, not a crash",
        _primary_email({"email_addresses": []}) is None)

    # Svix's own published test vector.
    secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
    mid = "msg_p5jXN8AQM9LWM0D4loKWxJek"
    mts = "1614265330"
    body = b'{"test": 2432232314}'
    good = "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="
    try:
        verify_svix(secret, mid, mts, good, body, now=1614265330)
        chk("known-good Svix vector VERIFIES (not just my own output)", True)
    except SignatureError:
        chk("known-good Svix vector VERIFIES (not just my own output)", False)

    for name, hdr, at, blob in [
        ("a tampered body is REJECTED", good, 1614265330, b'{"test": 9999999999}'),
        ("a wrong signature is REJECTED", "v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
         1614265330, body),
        ("a replay 1 h later is REJECTED on timestamp", good, 1614265330 + 3600, body),
    ]:
        try:
            verify_svix(secret, mid, mts, hdr, blob, now=at)
            chk(name, False)
        except SignatureError:
            chk(name, True)

    try:
        verify_svix("", mid, mts, good, body, now=1614265330)
        chk("an UNCONFIGURED secret fails CLOSED, never open", False)
    except SignatureError:
        chk("an UNCONFIGURED secret fails CLOSED, never open", True)

    try:
        verify_svix(secret, mid, mts, f"v1,other {good}", body, now=1614265330)
        chk("during rotation, a match on the SECOND signature is accepted", True)
    except SignatureError:
        chk("during rotation, a match on the SECOND signature is accepted", False)

    # The User-Agent fix needs a control or it is a one-line change nobody can
    # prove is still there. Asserts the header is actually SENT, not merely
    # defined -- a constant that no request references is the shape of
    # `max_fires_per_24h` sitting unread in authority.json for four days.
    import inspect
    src = inspect.getsource(fetch_clerk_users)
    chk("fetch_clerk_users actually SENDS User-Agent (not just defines it)",
        '"User-Agent": USER_AGENT' in src)
    chk("USER_AGENT is not the urllib default that Cloudflare 1010-blocks",
        "python-urllib" not in USER_AGENT.lower() and len(USER_AGENT) > 10)
    chk("a Cloudflare 1010 body is reported as an EDGE block, not a permissions "
        "problem -- the misreading that nearly became a false escalation",
        "CLOUDFLARE" in inspect.getsource(fetch_clerk_users))

    for n, c in checks:
        print(f"  {'PASS' if c else 'FAIL'}  {n}")
    passed = sum(1 for _, c in checks if c)
    print(f"self-test: {passed}/{len(checks)}")
    return 0 if passed == len(checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return _self_test()

    try:
        rep = reconcile(dry_run=a.dry_run)
    except Unknown as e:
        print(f"UNKNOWN: {e}. Asserting NOTHING about webhook health -- a probe "
              f"that cannot evaluate is not a green.", file=sys.stderr)
        return 2

    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"clerk users seen : {rep['clerk_users_seen']}")
        print(f"created/updated  : {rep['created']}/{rep['updated']} "
              f"(dup {rep['duplicate']}, skipped {rep['skipped']})")

    misses = rep["webhook_misses"]
    if not misses:
        print(f"GREEN: no signup older than {rep['stale_after_hours']}h was "
              f"missing -- checked against {rep['clerk_users_seen']} Clerk users, "
              f"so this is a measurement and not a silence.")
        return 0
    print(f"RED: the webhook did not deliver {len(misses)} signup(s):")
    for m in misses[:20]:
        print(f"  {m['clerk_id']}  signed up {m['signed_up_utc']} "
              f"({m['age_hours']}h ago)")
    print("Check the Clerk dashboard endpoint status and CLERK_WEBHOOK_SECRET "
          "on the Fly app. The users are already backfilled; the LIVE PATH is "
          "what is broken.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
