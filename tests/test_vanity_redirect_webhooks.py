"""The vanity-domain redirect must never stand between Svix and /webhooks/.

WHY THIS TEST EXISTS -- measured, not theorised (2026-09-13,
clerk-signup-reconcile-nightly):

    POST https://mcplookup.app/webhooks/clerk  ->  301  Location:
         https://mcprisky.io/webhooks/clerk

`mcplookup.app` is the HISTORIC PRIMARY -- the host the Clerk webhook endpoint
was most plausibly registered against when it was created around 2026-06-27 --
and `_vanity_redirect` has 301'd it ever since `CANONICAL_HOST` was pivoted to
`mcprisky.io`. A 301 is not a delivery: Svix does not follow redirects, and a
client that does follow one downgrades POST to GET and drops the body and the
`svix-*` signature headers. Either way the payload never reaches the verifier.

That is a complete explanation for [[FU-245]] -- 0 of 3 signups delivered in 78
days against an endpoint that is provably armed and healthy at the canonical
host (measured the same night: GET 405, unsigned POST 401, control path 404,
/health 200).

NOT PROVEN, and this docstring must not be read as claiming it: nobody here can
read which URL Clerk is actually configured with -- `GET /v1/webhooks/svix` is
POST-only and the Backend API exposes no endpoint listing (measured 2026-08-11).
So this is a cheap cure for a live defect that WOULD cause the observed
symptom, not a diagnosis of it. The cure is self-falsifying in the good way: if
a row with `clerk_synced_via='webhook'` appears after it deploys, the hypothesis
was right; if the drought continues, it was not and the Clerk-side read is still
owed.

Both assertions below were observed RED against the unpatched middleware before
the fix was written. An assertion never seen fail is UNPROVEN, not passing.
"""
from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient

ALIAS = "mcplookup.app"          # the historic primary, still a hosted domain
CANON = "mcprisky.io"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("CANONICAL_HOST", CANON)
    import app.main as m
    importlib.reload(m)          # the host lists are computed at import time
    # raise_server_exceptions=False: we assert on the redirect layer only, and a
    # downstream 500 from an unsigned body is not this test's business.
    return TestClient(m.app, raise_server_exceptions=False)


def test_webhook_post_on_alias_host_is_not_redirected(client):
    """The regression itself: a POST to /webhooks/* on an alias must be SERVED.

    The invariant asserted is EQUIVALENCE WITH THE CANONICAL HOST, not a
    literal status code. The route's own answer depends on deployment state --
    401 in prod where CLERK_WEBHOOK_SECRET is set, 503 `clerk webhook not
    configured` in a test env where it is not -- and pinning either one would
    make this test a statement about the environment instead of about the
    redirect layer. Equivalence is the property that actually matters and it
    holds in both: whatever the verifier does, the alias must get the same.
    """
    alias = client.post("/webhooks/clerk", headers={"host": ALIAS},
                        json={"type": "probe"}, follow_redirects=False)
    canon = client.post("/webhooks/clerk", headers={"host": CANON},
                        json={"type": "probe"}, follow_redirects=False)

    assert alias.status_code not in (301, 302, 303, 307, 308), (
        "the vanity redirect intercepted a webhook POST on %s (got %s -> %s). "
        "Svix does not follow redirects; this is a dropped delivery."
        % (ALIAS, alias.status_code, alias.headers.get("location"))
    )
    assert alias.status_code == canon.status_code, (
        "the webhook route answers %s on %s but %s on %s -- the endpoint must "
        "behave identically on every hosted domain, because which one Clerk is "
        "configured with is not readable from here."
        % (alias.status_code, ALIAS, canon.status_code, CANON))


def test_non_get_redirect_preserves_the_method(client):
    """Belt and braces: any surviving alias redirect must use 308, not 301.

    301/302 permit a client to rewrite the method to GET. 308 does not. This is
    the difference between a retried POST and a silently swallowed one.
    """
    r = client.post("/some/other/path", headers={"host": ALIAS},
                    json={}, follow_redirects=False)
    if r.status_code in (301, 302, 303, 307, 308):
        assert r.status_code == 308, (
            "a non-GET alias redirect returned %s; only 308 preserves the method"
            % r.status_code)


def test_get_on_alias_still_redirects_to_canonical(client):
    """The NEGATIVE CONTROL. Without this, a fix that simply deleted the
    middleware would pass both tests above. Canonicalisation must survive."""
    r = client.get("/pricing", headers={"host": ALIAS}, follow_redirects=False)
    assert r.status_code in (301, 308), (
        "alias canonicalisation was lost entirely (got %s) -- the fix removed "
        "more than it should have" % r.status_code)
    assert r.headers["location"] == "https://%s/pricing" % CANON


def test_canonical_host_is_never_redirected(client):
    """Second control: the canonical host must not redirect to itself."""
    r = client.get("/pricing", headers={"host": CANON}, follow_redirects=False)
    assert r.status_code not in (301, 302, 303, 307, 308)
