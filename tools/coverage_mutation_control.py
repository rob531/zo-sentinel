#!/usr/bin/env python3
"""R4 negative control for the corroboration branch in coverage_attribute.py.

Mutates the FILE on disk and runs `--self-test` in a SUBPROCESS -- never a
module object, because the subject reads its own copy off disk. Each mutant
must go RED with a NAMED assertion, not a traceback: rc-from-a-crash reads as a
verdict and is how a harness goes blind in the direction it was built to see.
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

TARGET = pathlib.Path(r"D:\zo\_lanes\ops-audit\tools\coverage_attribute.py")

MUTANTS = [
    (
        "delete the whole corroboration branch (back to v1 behaviour)",
        'if quiet and commits_by_day:',
        'if False:',
    ),
    (
        "corroborate everything -- never emit UNCORROBORATED_QUIET",
        'row["verdict"] = "UNCORROBORATED_QUIET"',
        'pass',
    ),
    (
        "invert the quiet comparison on the corroborating store",
        'if c < g_quiet_below:',
        'if c >= g_quiet_below:',
    ),
    (
        "a silent corroborator manufactures corroboration",
        'row["why"] += "; NOT corroborated -- no second store available"',
        'row["corroborated"] = True',
    ),
    (
        "drop the corroborator's own span guard",
        'if date < g_days[0] or date > g_days[-1]:',
        'if False:',
    ),
]


def run_selftest(path):
    p = subprocess.run([sys.executable, str(path), "--self-test"],
                       capture_output=True, text=True, timeout=180)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    src = TARGET.read_text(encoding="utf-8")

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="covmut_"))
    base = tmpdir / "coverage_attribute.py"
    base.write_text(src, encoding="utf-8", newline="\n")

    rc, out = run_selftest(base)
    if rc != 0:
        print("ABORT: the UNMUTATED baseline does not pass -- nothing below it")
        print("       means anything. rc=%d" % rc)
        print(out[-1500:])
        return 2
    print("baseline: self-test PASSES (rc=0)\n")

    failures = 0
    for i, (name, old, new) in enumerate(MUTANTS, 1):
        n = src.count(old)
        if n != 1:
            print("  %d. %-62s VACUOUS -- anchor matched %d time(s)" % (i, name, n))
            failures += 1
            continue
        mut = tmpdir / ("mutant_%d.py" % i)
        mut.write_text(src.replace(old, new, 1), encoding="utf-8", newline="\n")
        rc, out = run_selftest(mut)
        named = [ln for ln in out.splitlines() if ln.startswith("FAIL:")]
        if rc == 0:
            print("  %d. %-62s DECORATION -- mutant still PASSES" % (i, name))
            failures += 1
        elif rc == 2 or ("Traceback" in out and not named):
            print("  %d. %-62s CRASH, not a verdict (rc=%d)" % (i, name, rc))
            failures += 1
        elif not named:
            print("  %d. %-62s RED but with NO named assertion" % (i, name))
            failures += 1
        else:
            print("  %d. %-62s GUARDED -- %d named assertion(s)" % (i, name, len(named)))
            print("        e.g. %s" % named[0][:150])

    shutil.rmtree(tmpdir, ignore_errors=True)
    print()
    if failures:
        print("%d of %d mutant(s) were NOT caught by a named assertion -- rc=1"
              % (failures, len(MUTANTS)))
        return 1
    print("all %d mutant(s) observed RED with a named assertion: the "
          "corroboration branch is GUARDED, not decoration" % len(MUTANTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
