#!/usr/bin/env python3
"""accept_gate.py -- did the prod deploy we just fired actually land, and is it healthy?

The DEPLOY side of the fire has been code since ops/host/deploy_prod.ps1. The
ACCEPTANCE side -- the part that decides whether to keep the release or roll it
back -- was still prose in D:\\zo\\Zocomputer Agents\\prod_deploy_staged.md:

    "poll /health + /version + /spine/health for 90s; PASS = ok:true +
     empty failures[] + 200 + git_sha == <sha>"

...followed by a loop that printed twenty-seven JSON blobs and asserted nothing.
A human read them and judged. That is the same shape as the fire-a-later-sha rule
FU-155 replaced: a rule that twelve stages had READ and none had EXECUTED. Prose
accumulates authority by repetition, not by being right.

This makes the verdict mechanical and, critically, REUSABLE by three callers that
were each re-deriving it: the chairman's one-click, deploy_prod.ps1's own poll
loop, and prod-drift-sentinel's step-7 post-fire verify.

  exit 0  ACCEPT -- /health 200, /version.git_sha == the sha we fired,
                    /spine/health 200 with ok:true and an EMPTY failures[].
  exit 1  REJECT -- reachable, but at least one assertion failed. Reasons printed.
  exit 2  ERROR  -- could not establish the answer (unreachable, non-JSON, timeout).
                    NEVER readable as ACCEPT. A probe that cannot evaluate is not
                    a green -- and it is not a red either.

WHY git_sha IS A HARD ASSERTION, NOT A NICETY
prod served "git_sha":"unknown" for its entire life because no deploy ever passed
--build-arg GIT_SHA (the Dockerfile has declared ARG GIT_SHA since before v64).
Without it, "is the running image the tree that was gated?" is unanswerable and
drift has to be INFERRED from a release timestamp. deploy_prod.ps1 now always
passes the arg, so this assertion is evaluable -- but if a future deploy path
forgets it, git_sha reads "unknown", and this gate must REJECT rather than shrug.
An assertion that silently degrades to un-evaluable is how a gate becomes theatre.

HONEST CAVEAT (FU-114) -- AND THE ARITHMETIC THAT WAS MISSING FROM IT
An empty failures[] means every active service IMPORTED. Some active services
declare no router: the spine buckets those as `skipped_no_router`, NOT as
mounted, so they are registered, healthy-looking and serve nothing. Green here
means "mounts cleanly", never "serves traffic". This gate does not and cannot
close FU-114; it refuses to pretend otherwise.

What it now also refuses to do is MISREPORT the split. Until 2026-07-30 the
ACCEPT line read "(31 services mounted)" -- it printed `service_count`, which is
the DECLARED total, and labelled it the mounted count. The live v65 payload is
`mounted` 27 + `skipped_no_router` 4 + `failures` 0 == `service_count` 31, so the
number in this gate's own success line had been wrong against every prod payload
since v65, and the caveat below it hardcoded the word "Four". Twenty-eight tests
did not catch either, because the test fixture carried no `mounted` key at all:
the field that would have contradicted the claim was absent from the synthetic
input. That is FU-114's R3 shape exactly -- the failure bucket went to zero, the
check genuinely RAN, and a SECOND bucket absorbed four services while nothing in
the verdict line added the two together.

The buckets are now derived from the payload and printed. A bucket the payload
omits is UNKNOWN, never 0 (R6): an older prod that does not report `mounted`
must not be described as having mounted nothing. And a remainder that does not
sum is reported LOUDLY but does NOT change the verdict -- a reporting-layer
arithmetic note must never be able to roll back a healthy prod.

READ BEFORE ROLLING BACK
/spine/health ok:false with a populated failures[] is the fail-loud spine WORKING.
The app still serves 200 and the mounted services still work. That is a
fix-forward condition. Roll back for a 5xx or a WRONG git_sha -- those mean the
release is not the thing that was verified.

Usage:
    python tools/accept_gate.py --sha <40-char-sha>                  # poll 120s
    python tools/accept_gate.py --sha <40-char-sha> --once           # single probe
    python tools/accept_gate.py --sha <sha> --json                   # machine output
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://mcprisky.io"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_INTERVAL_SECONDS = 10
HTTP_TIMEOUT_SECONDS = 20

ACCEPT, REJECT, ERROR = 0, 1, 2
_VERDICT_NAME = {ACCEPT: "ACCEPT", REJECT: "REJECT", ERROR: "ERROR"}

# stdlib urllib on purpose: `requests` is not a declared dependency of this repo,
# and an undeclared import is how treewalk-smoke died silently for weeks (FU-084).


class ProbeError(Exception):
    """A surface could not be read or parsed. Never collapses into REJECT."""


def _count_or_unknown(body, key):
    """len(body[key]) -- or None when the key is absent. Absent is UNKNOWN, not 0."""
    val = body.get(key)
    if val is None:
        return None
    try:
        return len(val)
    except TypeError:
        return None


def spine_buckets(spine_body):
    """Decompose /spine/health into the buckets prod actually reports.

    Live v65 payload, measured 2026-07-30T01:50Z from https://mcprisky.io:
        service_count 31, mounted[27], skipped_no_router[4], failures[0].

    `failed` deliberately keeps the gate's existing "absent failures[] == empty"
    reading rather than becoming UNKNOWN, because that reading is load-bearing
    for the ACCEPT verdict and this change is reporting-only.
    """
    body = spine_body or {}
    declared = body.get("service_count")
    mounted = _count_or_unknown(body, "mounted")
    inert = _count_or_unknown(body, "skipped_no_router")
    failed = len(body.get("failures") or [])
    unaccounted = None
    if isinstance(declared, int) and mounted is not None and inert is not None:
        unaccounted = declared - (mounted + inert + failed)
    return {
        "declared": declared,
        "mounted": mounted,
        "inert": inert,
        "failed": failed,
        "unaccounted": unaccounted,
        "inert_names": list(body.get("skipped_no_router") or []),
    }


def describe_buckets(b):
    """One human phrase that never claims a count the payload did not supply."""
    declared = b["declared"] if b["declared"] is not None else "?"
    if b["mounted"] is None:
        head = "mounted count UNKNOWN (payload reports no 'mounted' key); %s declared" % declared
    else:
        head = "%d of %s mounted" % (b["mounted"], declared)
    inert = "inert count UNKNOWN" if b["inert"] is None else "%d declared no router (inert)" % b["inert"]
    return "%s, %s, %d failed" % (head, inert, b["failed"])


def arithmetic_note(b):
    """A LOUD line when the buckets do not sum -- and never a verdict change."""
    if b["unaccounted"] in (None, 0):
        return None
    return (
        "ARITHMETIC: service_count %s but mounted %s + inert %s + failed %d leaves %d "
        "UNACCOUNTED -- a bucket this gate cannot see is absorbing services. Reported, "
        "NOT held against the release: a reporting-layer remainder must not roll back a "
        "healthy prod." % (
            b["declared"], b["mounted"], b["inert"], b["failed"], b["unaccounted"],
        )
    )


def evaluate(health, version, spine, expected_sha):
    """Pure decision function -- no I/O, so it is testable without a live prod.

    Each argument is a (status_code, parsed_json_or_None) tuple.
    Returns (verdict, reasons) where reasons is a list of human-readable strings.
    ERROR wins over REJECT: an unread surface must never be reported as a red,
    because "we could not tell" and "it is broken" call for different actions.
    """
    reasons = []
    errors = []

    h_status, h_body = health
    v_status, v_body = version
    s_status, s_body = spine

    for name, status, body in (
        ("/health", h_status, h_body),
        ("/version", v_status, v_body),
        ("/spine/health", s_status, s_body),
    ):
        if status is None:
            errors.append("%s: unreachable" % name)
        elif body is None:
            errors.append("%s: HTTP %s but body was not JSON" % (name, status))

    if errors:
        return ERROR, errors

    if h_status != 200:
        reasons.append("/health returned HTTP %s (expected 200)" % h_status)

    got_sha = (v_body or {}).get("git_sha")
    if got_sha != expected_sha:
        if got_sha in (None, "", "unknown"):
            reasons.append(
                "/version.git_sha is %r -- the running image cannot identify itself, "
                "so 'is prod the tree that was gated?' is UNANSWERABLE. The deploy "
                "almost certainly omitted --build-arg GIT_SHA." % (got_sha,)
            )
        else:
            reasons.append(
                "/version.git_sha is %s but we fired %s -- prod is running a "
                "DIFFERENT tree than the one that was gated." % (got_sha, expected_sha)
            )

    if s_status != 200:
        reasons.append("/spine/health returned HTTP %s (expected 200)" % s_status)

    spine_body = s_body or {}
    failures = spine_body.get("failures") or []
    if spine_body.get("ok") is not True:
        reasons.append("/spine/health ok is %r (expected true)" % (spine_body.get("ok"),))
    if failures:
        named = []
        for f in failures:
            if isinstance(f, dict):
                named.append(str(f.get("service") or f.get("import_path") or f))
            else:
                named.append(str(f))
        reasons.append(
            "/spine/health failures[] is not empty: %d of %s services -- %s"
            % (len(failures), spine_body.get("service_count", "?"), ", ".join(named))
        )

    if reasons:
        return REJECT, reasons

    buckets = spine_buckets(spine_body)
    accepted = [
        "/health 200; /version.git_sha == %s; /spine/health 200 ok:true failures[] empty "
        "(%s)" % (expected_sha, describe_buckets(buckets))
    ]
    note = arithmetic_note(buckets)
    if note:
        accepted.append(note)
    return ACCEPT, accepted


def _fetch(url):
    """Return (status, parsed_json_or_None). Raises ProbeError if unreachable."""
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            status = resp.getcode()
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read().decode("utf-8", "replace")
        except Exception:
            raw = ""
    except Exception as exc:
        raise ProbeError("%s: %s" % (url, exc))
    try:
        return status, json.loads(raw)
    except Exception:
        return status, None


def probe_once(base_url, expected_sha):
    base = base_url.rstrip("/")
    try:
        health = _fetch(base + "/health")
        version = _fetch(base + "/version")
        spine = _fetch(base + "/spine/health")
    except ProbeError as exc:
        return ERROR, [str(exc)], {}
    verdict, reasons = evaluate(health, version, spine, expected_sha)
    buckets = spine_buckets(spine[1])
    observed = {
        "health_status": health[0],
        "version": version[1],
        "spine_status": spine[0],
        "spine_ok": (spine[1] or {}).get("ok"),
        "spine_service_count": buckets["declared"],
        "spine_failure_count": buckets["failed"],
        # FU-114: the three buckets, so a caller recording this dict cannot mistake
        # the DECLARED total for the mounted count the way prod_deploy_state.json did.
        "spine_mounted_count": buckets["mounted"],
        "spine_inert_count": buckets["inert"],
        "spine_inert_services": buckets["inert_names"],
        "spine_unaccounted_count": buckets["unaccounted"],
    }
    return verdict, reasons, observed


def poll(base_url, expected_sha, timeout_seconds, interval_seconds, once, log=print):
    """Poll until ACCEPT or the deadline. Returns (verdict, reasons, observed).

    A deploy is in flight while we poll, so a REJECT or an ERROR early on is
    EXPECTED -- the old release is still serving. Only the LAST verdict before
    the deadline is the answer. Bailing on the first red would reject every
    healthy deploy that took longer than one probe to swap over.
    """
    deadline = time.time() + (0 if once else timeout_seconds)
    verdict, reasons, observed = ERROR, ["no probe completed"], {}
    attempt = 0
    while True:
        attempt += 1
        verdict, reasons, observed = probe_once(base_url, expected_sha)
        log(
            "[accept_gate] probe %d: %s -- health=%s version.git_sha=%s spine=%s ok=%s failures=%s"
            % (
                attempt,
                _VERDICT_NAME[verdict],
                observed.get("health_status"),
                (observed.get("version") or {}).get("git_sha"),
                observed.get("spine_status"),
                observed.get("spine_ok"),
                observed.get("spine_failure_count"),
            )
        )
        if verdict == ACCEPT or once or time.time() >= deadline:
            break
        time.sleep(min(interval_seconds, max(0, deadline - time.time())))
    return verdict, reasons, observed


def _accept_caveat(observed):
    """The FU-114 caveat, counted FROM THE PAYLOAD instead of hardcoding "Four".

    The old text said "Four of the 31 declare no router and mount clean". Two
    things were wrong: the spine does not count them as mounted (it buckets them
    as skipped_no_router, so mounted was 27, not 31), and "Four" was a literal
    that would keep reading four if a fifth inert service appeared.
    """
    inert = observed.get("spine_inert_count")
    declared = observed.get("spine_service_count")
    mounted = observed.get("spine_mounted_count")
    lead = "CAVEAT (FU-114): an empty failures[] means every active service IMPORTED. "
    if inert is None:
        return (
            lead + "This payload does not report a skipped_no_router bucket, so how many "
            "active services declare no router is UNKNOWN -- not zero. Green means "
            "imports cleanly, not serves traffic."
        )
    if inert == 0:
        return (
            lead + "No active service is bucketed skipped_no_router in this payload. "
            "Green still means mounts cleanly, not serves traffic."
        )
    names = ", ".join(observed.get("spine_inert_services") or []) or "(unnamed)"
    return (
        lead + "%d of %s declare no router: the spine buckets those as skipped_no_router, "
        "NOT as mounted, so mounted is %s -- %s. Green means mounts cleanly, never "
        "serves traffic; FU-114 stays open." % (
            inert,
            declared if declared is not None else "?",
            mounted if mounted is not None else "UNKNOWN",
            names,
        )
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sha", required=True, help="the 40-char sha that was fired")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    ap.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL_SECONDS)
    ap.add_argument("--once", action="store_true", help="single probe, no polling")
    ap.add_argument("--rollback-image", default="", help="printed in the REJECT hint")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    sha = args.sha.strip().lower()
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        print(
            "[accept_gate] ERROR: --sha must be a full 40-char hex sha (got %r). "
            "A short sha makes the deployed identity ambiguous." % args.sha
        )
        return ERROR

    log = (lambda *_a, **_k: None) if args.json else print
    verdict, reasons, observed = poll(
        args.base_url, sha, args.timeout_seconds, args.interval_seconds, args.once, log=log
    )

    if args.json:
        print(
            json.dumps(
                {
                    "verdict": _VERDICT_NAME[verdict],
                    "exit_code": verdict,
                    "sha": sha,
                    "base_url": args.base_url,
                    "reasons": reasons,
                    "observed": observed,
                },
                indent=2,
            )
        )
        return verdict

    print("")
    print("VERDICT: %s" % _VERDICT_NAME[verdict])
    for r in reasons:
        print("  - %s" % r)
    if verdict == ACCEPT:
        print("")
        print(_accept_caveat(observed))
    elif verdict == REJECT:
        print("")
        print(
            "READ BEFORE ROLLING BACK: ok:false with a populated failures[] is the "
            "fail-loud spine WORKING -- the app serves 200 and mounted services work, "
            "and that is a FIX-FORWARD condition. Roll back for a 5xx or a WRONG "
            "git_sha; those mean prod is not the thing that was verified."
        )
        if args.rollback_image:
            print("ROLLBACK: flyctl deploy --app mcplookup --image %s --yes" % args.rollback_image)
    else:
        print("")
        print(
            "ERROR is not a red. Prod could not be read, so nothing was established. "
            "Do not roll back on this -- re-probe, and check whether the surface is "
            "reachable at all."
        )
    return verdict


if __name__ == "__main__":
    sys.exit(main())
