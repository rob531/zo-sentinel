#!/usr/bin/env python3
"""Assert the RENDERED UI names the build that is actually running.

    python tools/ui_version_assert.py --base-url https://mcprisky.io --expect <sha>
    python tools/ui_version_assert.py --base-url https://mcprisky.io          # vs /version

WHAT THIS ADDS OVER accept_gate's EXISTING CHECK
------------------------------------------------
`accept_gate` already asserts `/version.git_sha == the sha we fired`. That proves
the API layer is the gated tree. It says NOTHING about the HTML: the badge could
be missing, stale in a cached page, or injected on one route and not another, and
every existing gate would stay green. The surface a person triages from would
then be silently unstamped -- which is the condition this whole change exists to
end, so it is the condition that has to be asserted, not assumed.

TWO OBSERVATIONS, NOT ONE (R4: an assertion never seen red is unproven)
`--expect` compares the badge to the sha we FIRED. Without it the badge is
compared to `/version` on the SAME host, which catches a stale or missing badge
but cannot catch "both surfaces agree and both are the wrong build". The deploy
path passes --expect; a spot check may omit it. The output always says which
comparison it made, because a check that does not name its own strength invites
being read as the stronger one.

EXIT CODES
    0  PASS     every checked route carries a badge whose sha matches
    1  FAIL     a route rendered without a badge, or with the wrong sha
    2  ERROR    a route was unreachable / non-HTML -- UNKNOWN, not a red (R6).
                "we could not tell" and "it is broken" call for different actions.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.request

DEFAULT_ROUTES = ("/", "/app", "/disclaimer")
BADGE_RE = re.compile(r'id="zo-build-badge"[^>]*data-build-sha="([^"]*)"')
TIMEOUT = 15

PASS, FAIL, ERROR = 0, 1, 2


def fetch(url: str):
    """(status, text) -- or (None, reason). Never raises: an unreachable route
    is an ERROR verdict to be reported, not a traceback to be read."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "zo-ui-version-assert"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def badge_sha(page_html: str):
    """The sha the page CLAIMS, or None if it carries no badge.

    Matches the id and the attribute together. A page that happens to contain
    the string `data-build-sha` in unrelated markup is not a stamped page, and
    a matcher loose enough to be fooled by that would report a badge the
    triager cannot see.
    """
    m = BADGE_RE.search(page_html or "")
    return m.group(1) if m else None


def check(base_url: str, routes, expect: str | None):
    base = base_url.rstrip("/")
    findings, errors, seen = [], [], {}

    for route in routes:
        status, body = fetch(base + route)
        if status is None:
            errors.append("%s: unreachable -- %s" % (route, body))
            continue
        if status != 200:
            errors.append("%s: HTTP %s" % (route, status))
            continue
        sha = badge_sha(body)
        if sha is None:
            findings.append(
                "%s: rendered 200 with NO build badge -- this route is not going "
                "through app.build_badge.inject()" % route)
            continue
        seen[route] = sha
        if sha == "unknown":
            findings.append(
                "%s: badge reads 'unknown' -- the image was built without "
                "--build-arg GIT_SHA and cannot identify itself" % route)

    reference, how = expect, "the sha that was fired (--expect)"
    if reference is None:
        status, body = fetch(base + "/version")
        if status is None or status != 200:
            errors.append("/version unreachable -- no reference sha to compare against")
        else:
            try:
                import json
                reference = (json.loads(body) or {}).get("git_sha")
            except Exception:
                errors.append("/version returned non-JSON -- no reference sha")
        how = "/version on the same host (WEAKER: cannot catch both surfaces agreeing on the wrong build)"

    if reference:
        for route, sha in seen.items():
            if sha != reference and sha != "unknown":
                findings.append(
                    "%s: badge says %s but the reference is %s -- the page a "
                    "human reads names a DIFFERENT build than %s"
                    % (route, sha, reference, how))

    if errors:
        return ERROR, errors, seen, how
    if findings:
        return FAIL, findings, seen, how
    return PASS, ["%d/%d routes stamped, all == %s, compared against %s"
                  % (len(seen), len(routes), reference, how)], seen, how


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--expect", default=None,
                    help="the sha that was fired; omit to compare against /version")
    ap.add_argument("--routes", nargs="*", default=list(DEFAULT_ROUTES))
    a = ap.parse_args(argv)

    verdict, lines, seen, how = check(a.base_url, a.routes, a.expect)
    label = {PASS: "PASS", FAIL: "FAIL", ERROR: "ERROR"}[verdict]
    print("[ui_version_assert] %s" % label)
    for line in lines:
        print("  - %s" % line)
    for route, sha in sorted(seen.items()):
        print("  observed %-14s %s" % (route, sha))
    return verdict


if __name__ == "__main__":
    sys.exit(main())
