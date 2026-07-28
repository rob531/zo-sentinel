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

HONEST CAVEAT (FU-114)
An empty failures[] means every active service IMPORTED and MOUNTED. Four of the
31 declare no router: they mount clean while serving nothing. Green here means
"mounts cleanly", never "serves traffic". This gate does not and cannot close
FU-114; it refuses to pretend otherwise.

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
    return ACCEPT, [
        "/health 200; /version.git_sha == %s; /spine/health 200 ok:true failures[] empty "
        "(%s services mounted)" % (expected_sha, spine_body.get("service_count", "?"))
    ]


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
    observed = {
        "health_status": health[0],
        "version": version[1],
        "spine_status": spine[0],
        "spine_ok": (spine[1] or {}).get("ok"),
        "spine_service_count": (spine[1] or {}).get("service_count"),
        "spine_failure_count": len((spine[1] or {}).get("failures") or []),
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
        print(
            "CAVEAT (FU-114): an empty failures[] means every active service MOUNTED. "
            "Four of the 31 declare no router and mount clean while serving nothing. "
            "Green means mounts cleanly, not serves traffic."
        )
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
