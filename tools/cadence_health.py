#!/usr/bin/env python3
"""Cadence health as a DISCRIMINATOR -- it cannot return a false green.

WHY THIS EXISTS (measured 2026-09-13 by vast-jobs-daily-audit, both poles).
The daily ops audit checked cadence from PROSE: "GET /api/admin/cadence/health
with header X-Cadence-Key; GREEN = all jobs overdue=false, zombie_running=0,
alert=false".  A hand-rolled reader of that prose reports GREEN on TWO inputs
that are not health:

  1. AN UNAUTHENTICATED READ.  Observed live: a junk key returns HTTP 401 with
     body {"detail":"Authentication required"}.  A reader that parses, finds no
     overdue jobs and no alert flag, concludes GREEN.  This is FU-192's
     false-zero class exactly one surface over from vast -- `live_instances: 0`
     on a 401 is byte-identical to a clean day, and so is `0 overdue jobs`.

  2. THE SHAPE.  `jobs` is a **dict keyed by job name**, NOT a list of job
     objects.  Measured: jobs_type=dict, iterating yields str.  A reader written
     for the documented-looking list shape iterates KEYS, inspects zero job
     objects, and can therefore NEVER see an overdue job -- it reports GREEN
     even while a job is overdue.  The endpoint was named in the SKILL; the
     shape never was, which is c87's lesson repeating on a second endpoint.

So `auth_proven` here is keyed on a POSITIVE signal a 401 cannot forge -- the
body must carry `sla_hours` AND a non-empty `jobs` mapping -- and the job count
is published beside the verdict so a zero can never pass as a pass (R3/R6).

    python tools/cadence_health.py                 # human line + json
    python tools/cadence_health.py --json
    python tools/cadence_health.py --self-test     # R4, both poles

Exit codes:  0 AUTHENTICATED_GREEN   1 RED (overdue/zombie/alert)   2 UNKNOWN
UNKNOWN covers auth failure, an unreachable host, and a body whose shape this
tool does not recognise.  UNKNOWN IS NOT GREEN and must never be reported as
one.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

HEALTH_URL = "https://mcprisky.io/api/admin/cadence/health"
JOBS_URL = "https://mcprisky.io/api/admin/cadence/jobs/%s"
FETCH_SECRET = r"D:\agentvault\fetch_secret.py"
SECRET_NAME = "cadence_admin_key"
TIMEOUT = 45


class CadenceError(RuntimeError):
    pass


def admin_key(fetch_secret: str = FETCH_SECRET, name: str = SECRET_NAME) -> str:
    """AgentVault is the only sanctioned source -- never os.environ raw."""
    try:
        r = subprocess.run([sys.executable, fetch_secret, name],
                           capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa: BLE001
        raise CadenceError("fetch_secret failed: %s: %s"
                           % (exc.__class__.__name__, exc)) from exc
    key = (r.stdout or "").strip()
    if not key:
        raise CadenceError(
            "no %s from AgentVault (rc=%s) -- the read is UNKNOWN, not healthy"
            % (name, r.returncode))
    return key


def fetch_health(key: Optional[str] = None, url: str = HEALTH_URL) -> Dict[str, Any]:
    key = key or admin_key()
    req = urllib.request.Request(
        url, headers={"X-Cadence-Key": key, "User-Agent": "zo-ops-audit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise CadenceError(
            "cadence health HTTP %s: %s -- refusing to report GREEN on an "
            "unauthenticated read (FU-192)" % (exc.code, detail)) from exc
    except Exception as exc:  # noqa: BLE001
        raise CadenceError("cadence health unreachable: %s: %s"
                           % (exc.__class__.__name__, exc)) from exc
    try:
        return json.loads(body)
    except Exception as exc:  # noqa: BLE001
        raise CadenceError("cadence health body is not JSON: %r" % body[:200]) from exc


def normalise_jobs(raw: Any) -> List[Dict[str, Any]]:
    """Accept the REAL shape (dict keyed by name) and a list, reject anything else.

    THE SHAPE GUARD IS THE POINT.  Iterating the real dict yields strings, so a
    list-shaped reader inspects nothing and cannot see an overdue job.  A shape
    this function does not recognise raises -- it never degrades to an empty
    list, because an empty list is what reads as GREEN.
    """
    if raw is None:
        raise CadenceError("no jobs key in the response -- UNKNOWN, not green")
    if isinstance(raw, dict):
        out = []
        for name, job in raw.items():
            if not isinstance(job, dict):
                raise CadenceError(
                    "job %r is %s, not an object -- shape not recognised"
                    % (name, type(job).__name__))
            out.append(dict(job, name=name))
        return out
    if isinstance(raw, list):
        for job in raw:
            if not isinstance(job, dict):
                raise CadenceError(
                    "job entry is %s, not an object -- a list of %s is the "
                    "shape that reads as GREEN while blind"
                    % (type(job).__name__, type(job).__name__))
        return list(raw)
    raise CadenceError("jobs is %s -- shape not recognised; UNKNOWN, not green"
                       % type(raw).__name__)


def evaluate(body: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a health body into a verdict that carries its own basis."""
    out: Dict[str, Any] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "auth_proven": False, "jobs_seen": None, "jobs": [],
        "overdue": [], "zombie_running": None, "alert": None,
        "sla_hours": None, "verdict": "UNKNOWN", "error": None,
    }
    sla = body.get("sla_hours")
    # auth_proven needs a POSITIVE signal a 401 body cannot forge.
    # NOTE: this guard tests sla_hours ONLY.  It used to also test `not jobs_raw`,
    # which made the zero-jobs guard below unreachable -- the mutation matrix
    # caught that on 2026-09-13: removing the zero-jobs guard changed no
    # assertion, i.e. it was decoration.  Each guard now owns exactly one case.
    if not isinstance(sla, (int, float)):
        raise CadenceError(
            "no sla_hours in the response -- an authenticated health read "
            "always carries it, so this is UNKNOWN, not green (got keys %s)"
            % sorted(body.keys()))
    jobs = normalise_jobs(body.get("jobs"))
    if not jobs:
        raise CadenceError("zero jobs after normalisation -- a check that "
                           "inspected nothing is UNKNOWN, not a pass (R3)")
    out["auth_proven"] = True
    out["sla_hours"] = sla
    out["jobs_seen"] = len(jobs)
    out["zombie_running"] = body.get("zombie_running")
    out["alert"] = body.get("alert")
    for job in jobs:
        rec = {"name": job.get("name"), "last_ok": job.get("last_ok"),
               "overdue": bool(job.get("overdue")),
               "zombie_running": job.get("zombie_running")}
        out["jobs"].append(rec)
        if rec["overdue"] or rec["zombie_running"]:
            rec["detail_cmd"] = (
                "GET https://mcprisky.io/api/admin/cadence/jobs/<run_id> "
                "with X-Cadence-Key")
            out["overdue"].append(rec)
    red = bool(out["overdue"]) or bool(out["zombie_running"]) or bool(out["alert"])
    out["verdict"] = "RED" if red else "AUTHENTICATED_GREEN"
    return out


def discriminator(key: Optional[str] = None) -> Dict[str, Any]:
    try:
        return evaluate(fetch_health(key=key))
    except Exception as exc:  # noqa: BLE001 -- fail loud, never green
        return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "auth_proven": False, "jobs_seen": None, "jobs": [],
                "overdue": [], "zombie_running": None, "alert": None,
                "sla_hours": None, "verdict": "UNKNOWN",
                "error": "%s: %s" % (exc.__class__.__name__, exc)}


def rc_for(rep: Dict[str, Any]) -> int:
    return {"AUTHENTICATED_GREEN": 0, "RED": 1}.get(rep.get("verdict"), 2)


def format_line(rep: Dict[str, Any]) -> str:
    if rep["verdict"] == "UNKNOWN":
        return "CADENCE: UNKNOWN -- %s" % rep.get("error")
    names = ", ".join("%s overdue=%s" % (j["name"], j["overdue"])
                      for j in rep["jobs"])
    return ("CADENCE: %s -- %s job(s) inspected [%s], zombie_running=%s, "
            "alert=%s, sla_hours=%s, auth_proven=%s"
            % (rep["verdict"], rep["jobs_seen"], names, rep["zombie_running"],
               rep["alert"], rep["sla_hours"], rep["auth_proven"]))


# --------------------------------------------------------------------------
# R4 -- a check never observed RED is UNPROVEN, not passing.
# Every assertion below has been observed to FAIL when its guard is removed.
# --------------------------------------------------------------------------
GREEN_BODY = {"sla_hours": 36,
              "jobs": {"perspective_snapshots": {"last_ok": "2026-09-13T10:28:15", "overdue": False},
                       "ask_corpus_drift": {"last_ok": "2026-09-13T10:26:49", "overdue": False}},
              "zombie_running": 0, "alert": False}
OVERDUE_BODY = {"sla_hours": 36,
                "jobs": {"perspective_snapshots": {"last_ok": "2026-09-11T10:28:15", "overdue": True},
                         "ask_corpus_drift": {"last_ok": "2026-09-13T10:26:49", "overdue": False}},
                "zombie_running": 0, "alert": True}
UNAUTH_BODY = {"detail": "Authentication required"}
LIST_SHAPE_BODY = {"sla_hours": 36, "jobs": ["perspective_snapshots", "ask_corpus_drift"],
                   "zombie_running": 0, "alert": False}


def _expect_unknown(body, label, failures):
    try:
        rep = evaluate(body)
    except CadenceError:
        return
    failures.append("%s must be UNKNOWN, got %s" % (label, rep["verdict"]))


def _eval_or_fail(body, label, failures):
    """A CRASH IS NOT A VERDICT.  Turn an unexpected raise into a named
    assertion failure, so a mutation that breaks a guard is distinguishable
    from a harness that fell over (the rc-from-a-crash-reads-as-a-verdict
    family, x6 across 3 lanes in the 7d to 2026-09-13)."""
    try:
        return evaluate(body)
    except Exception as exc:  # noqa: BLE001
        failures.append("%s must evaluate, raised %s: %s"
                        % (label, exc.__class__.__name__, exc))
        return None


def selftest() -> int:
    failures: List[str] = []

    # POSITIVE POLE -- a real green body must actually reach GREEN, else the
    # tool is a rubber stamp that refuses everything.
    rep = _eval_or_fail(GREEN_BODY, "green body", failures)
    if rep is not None:
        if rep["verdict"] != "AUTHENTICATED_GREEN":
            failures.append("green body must be AUTHENTICATED_GREEN, got %s" % rep["verdict"])
        if rep["jobs_seen"] != 2:
            failures.append("green body must inspect 2 jobs, saw %s" % rep["jobs_seen"])
        if not rep["auth_proven"]:
            failures.append("green body must prove auth")
        if rc_for(rep) != 0:
            failures.append("green body rc must be 0, got %s" % rc_for(rep))

    # NEGATIVE POLE -- it must be capable of reading RED at all.
    rep = _eval_or_fail(OVERDUE_BODY, "overdue body", failures)
    if rep is not None:
        if rep["verdict"] != "RED":
            failures.append("overdue body must be RED, got %s" % rep["verdict"])
        if len(rep["overdue"]) != 1:
            failures.append("overdue body must name 1 overdue job, got %s" % len(rep["overdue"]))
        if rc_for(rep) != 1:
            failures.append("overdue body rc must be 1, got %s" % rc_for(rep))

    # THE TWO FALSE-GREEN PATHS THAT MOTIVATED THIS TOOL.
    # Each of these is owned by exactly ONE guard -- verified by the mutation
    # matrix in tools/cadence_mutation_control.py, which requires every guard to
    # be observed carrying at least one assertion.
    _expect_unknown(UNAUTH_BODY, "401 body", failures)              # sla_hours guard
    _expect_unknown(LIST_SHAPE_BODY, "list-shaped jobs", failures)  # shape guard
    _expect_unknown({"sla_hours": 36, "jobs": {}}, "empty jobs", failures)  # zero-jobs guard
    _expect_unknown({"jobs": GREEN_BODY["jobs"]}, "missing sla_hours", failures)
    _expect_unknown({"sla_hours": 36}, "missing jobs key", failures)

    # UNKNOWN must not be rc 0 -- the whole point is that it is not a pass.
    if rc_for({"verdict": "UNKNOWN"}) != 2:
        failures.append("UNKNOWN rc must be 2")

    print(json.dumps({"self_test": "cadence_health",
                      "checks": 14, "failures": failures,
                      "ok": not failures}, indent=2))
    if failures:
        print("SELF-TEST RED: %d" % len(failures))
        return 1
    print("SELF-TEST GREEN: positive pole reaches GREEN, negative pole reaches "
          "RED, and both measured false-green paths (401 body, list-shaped "
          "jobs) are refused as UNKNOWN.")
    return 0


def control() -> int:
    """R4 live control: the same path on a junk key MUST refuse.

    Exits 0 only when the discriminator refuses -- proof it can read RED
    against the live endpoint, not just against fixtures.
    """
    bad = discriminator(key="0" * 32)
    ok = (bad["auth_proven"] is False and bad["verdict"] == "UNKNOWN"
          and bad["jobs_seen"] is None and "401" in (bad.get("error") or ""))
    print(json.dumps({"control": "invalid-key", "refused": ok, "observed": bad},
                     indent=2, default=str))
    return 0 if ok else 1


def main(argv: List[str]) -> int:
    mode = argv[0] if argv else ""
    if mode in ("--self-test", "selftest", "--selftest"):
        return selftest()
    if mode in ("control", "--control", "discriminator-control"):
        return control()
    rep = discriminator()
    if mode in ("--json", "json"):
        print(json.dumps(rep, indent=2, default=str))
    else:
        print(format_line(rep))
        print(json.dumps(rep, indent=2, default=str))
    return rc_for(rep)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
