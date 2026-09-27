"""R4 MUTATION MATRIX for tools/cadence_health.py.

A self-test that has never been observed RED is UNPROVEN, not passing.  This
mutates the FILE (never a module object -- the subject reads its own copy off
disk) and runs each mutant in a SUBPROCESS, so no verdict is frozen by an
inlined import.

For each guard: remove it, and record whether --self-test goes RED.  A guard
whose removal changes nothing is carrying NO assertion -- it is decoration, and
that is a finding, not a pass.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Repo-relative, NOT a tower path: this runs on a CI runner too, and a hardcoded
# D:\ would make the gate pass by never executing (the 51% class -- the artifact
# you inspected is not the artifact that runs).
SRC = Path(__file__).resolve().parent / "cadence_health.py"
if not SRC.exists():  # pragma: no cover
    raise SystemExit("cadence_health.py not found beside %s -- refusing to "
                     "report a result about a file that is not there" % __file__)

MUTATIONS = {
    # the auth_proven positive signal a 401 cannot forge
    "auth_positive_signal": (
        '    if isinstance(sla, bool) or not isinstance(sla, (int, float)):',
        '    if False:'),
    # the shape guard: degrade to the naive list-shaped reader that motivated this
    "shape_guard": (
        '    if isinstance(raw, dict):',
        '    if False:'),
    # the "a check that inspected nothing is not a pass" guard (R3)
    "zero_jobs_guard": (
        '    if not jobs:',
        '    if False:'),
    # UNKNOWN must not map to rc 0
    "unknown_is_not_green": (
        '    return {"AUTHENTICATED_GREEN": 0, "RED": 1}.get(rep.get("verdict"), 2)',
        '    return {"AUTHENTICATED_GREEN": 0, "RED": 1}.get(rep.get("verdict"), 0)'),
    # --- added 2026-09-13 after an adversary showed these guards were UNMUTATED
    #     and therefore unproven: the matrix covered 4 of 8+ guards and claimed
    #     "every guard". Scope is now stated, and these four are covered.
    "zombie_and_alert_are_red_signals": (
        '    red = bool(out["overdue"]) or bool(out["zombie_running"]) or bool(out["alert"])',
        '    red = bool(out["overdue"])'),
    "absent_field_is_not_zero": (
        '        if field not in body:',
        '        if False:'),
    "per_job_overdue_required": (
        '    if "overdue" not in job:',
        '    if False:'),
    "bool_is_not_a_number": (
        '    if isinstance(sla, bool) or not isinstance(sla, (int, float)):',
        '    if not isinstance(sla, (int, float)):'),
}

base = SRC.read_text(encoding="utf-8")
results = {}
tmp = Path(tempfile.mkdtemp(prefix="cadence_mut_"))

# control-of-the-control: the UNMUTATED copy must be GREEN in this harness,
# otherwise a RED below proves nothing about the mutation.
clean = tmp / "clean.py"
clean.write_text(base, encoding="utf-8")
r = subprocess.run([sys.executable, str(clean), "--self-test"],
                   capture_output=True, text=True, timeout=120)
results["_unmutated_baseline"] = {"rc": r.returncode,
                                  "expected_rc": 0,
                                  "ok": r.returncode == 0}

for name, (old, new) in MUTATIONS.items():
    if old not in base:
        results[name] = {"applied": False,
                         "error": "anchor not found -- mutation vacuous, "
                                  "this row proves NOTHING"}
        continue
    mutant = tmp / (name + ".py")
    mutant.write_text(base.replace(old, new, 1), encoding="utf-8")
    r = subprocess.run([sys.executable, str(mutant), "--self-test"],
                       capture_output=True, text=True, timeout=120)
    fails = []
    try:
        fails = json.loads(r.stdout.split("SELF-TEST")[0]).get("failures", [])
    except Exception:
        pass
    # rc != 0 IS NOT PROOF. An adversary probe on 2026-09-13 showed a mutant that
    # crashes at import, or inside evaluate(), also exits non-zero with ZERO
    # assertions fired -- so "the guard carried an assertion" and "the mutant did
    # not even run" scored identically. That is rc-from-a-crash-reads-as-a-verdict
    # inside the very tool built to police it. The proof is a NAMED assertion.
    results[name] = {
        "applied": True,
        "rc": r.returncode,
        "assertions_that_fired": fails,
        "proved": bool(fails),
        "went_red": r.returncode != 0,
    }
    if r.returncode != 0 and not fails:
        results[name]["note"] = (
            "NON-ZERO BUT NO NAMED ASSERTION -- indistinguishable from a mutant "
            "that failed to import. Not counted as proof.")

print(json.dumps(results, indent=2))

shutil.rmtree(tmp, ignore_errors=True)

baseline_ok = results["_unmutated_baseline"]["ok"]
dead = [k for k, v in results.items()
        if k != "_unmutated_baseline" and v.get("applied") and not v.get("proved")]
vacuous = [k for k, v in results.items() if v.get("applied") is False]

print()
if not baseline_ok:
    print("HARNESS RED: the unmutated copy did not pass -- no mutation result below means anything.")
    sys.exit(2)
if vacuous:
    print("VACUOUS MUTATION(S) -- anchor not found, proves nothing: %s" % ", ".join(vacuous))
    sys.exit(2)
if dead:
    print("UNPROVEN GUARD(S) -- removing these changed no assertion: %s" % ", ".join(dead))
    sys.exit(1)
covered = len(MUTATIONS)
print("R4 GREEN: %d of %d mutated guards each produced a NAMED assertion failure, "
      "and the unmutated baseline is GREEN.\n"
      "SCOPE (state it, do not overclaim): this matrix covers the %d guards listed "
      "in MUTATIONS. A guard not listed here is UNPROVEN, not proven -- v1 claimed "
      "'every guard' while covering 4 of 8+, and an adversary found two of the "
      "uncovered ones carrying no assertion at all."
      % (covered, covered, covered))
sys.exit(0)
