#!/usr/bin/env python3
"""Schema-PRM CI gate (GraphifyKL backstop).

Fails a PR whose added/modified ROOT-LEVEL Python modules reference the real
app.models with a hallucinated schema: unknown constructor kwargs, unknown model
attribute access, or an inline declarative_base()/mock model. Built on the
GraphifyKL schema layer (schema_kl.py). Complements the no-hollow gate (which
catches mock DBs) and the runtime self-test gate (which catches runtime errors)
by deterministically catching schema drift before merge.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.getcwd())
import schema_kl  # noqa: E402

BASE = os.environ.get("BASE_REF", "origin/main")
SKIP = ("app/", "tests/", "zo_sentinel/", "scripts/", "migrations/", "infra/",
        "goose_recipes/", ".github/", "_scratch/", "archive/", "quarantine/")

#: WHERE THE ENGINE ACTUALLY EMITS (#4076).
#:
#: This gate used to skip any path containing a "/" -- root-level single-file
#: modules only. The engine emits into services/**, so a REQUIRED check was
#: inspecting 2.4% of the .py output it exists to guard, and its green tick read
#: as coverage. Measured over 1,568 `build:` commits in 30 days.
#:
#: These directories are now in scope. They are NOT yet blocking: see
#: BLOCKING_PATHS below.
WIDENED = ("services/", "auto_emitted_service/", "service_package/", "tools/")

#: THE ARMING RULE, and why widening is not the same as arming.
#:
#: This is a required status check. Turning newly-inspected paths red on the day
#: they are first inspected would block every open PR on a backlog nobody has
#: triaged -- which is not enforcement, it is an outage, and it is how a gate
#: earns itself an off switch. referent-verify made this exact split for its
#: columns check and it is the reason that arming held.
#:
#: So: root-level stays BLOCKING (it always was), and the widened paths are
#: REPORT-ONLY until their backlog is cleared. The count prints on every run, so
#: it cannot be quietly forgotten, and promoting them is a one-line change here
#: once the number is zero.
BLOCKING_PATHS = ("",)          # "" == root-level, the historical scope
REPORT_ONLY_PATHS = WIDENED


def _in_scope(f: str) -> bool:
    if not f.endswith(".py") or any(f.startswith(d) for d in SKIP):
        return False
    return "/" not in f or f.startswith(WIDENED)


def is_blocking(f: str) -> bool:
    """Root-level files block; the widened paths report only. See #4076."""
    return "/" not in f


def candidate_files():
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=AM", f"{BASE}...HEAD"],
                       capture_output=True, text=True)
    return [f.strip() for f in r.stdout.splitlines()
            if f.strip() and _in_scope(f.strip())]


def get_kl():
    """Prefer the live introspection of app.models (ground truth); fall back to the
    committed KL artifact if the app isn't importable in this environment."""
    try:
        return schema_kl.build_schema_kl()
    except Exception as e:
        print(f"[schema-prm] build_schema_kl failed ({e}); using committed KL", file=sys.stderr)
        return schema_kl.load_schema_kl()


def main():
    # KL FRESHNESS -- blocking.
    #
    # graphify-out/schema_kl.json is a COMMITTED artifact that this gate (and
    # goose_runner's) falls back to when app.models cannot be imported. Built
    # from a stale checkout it is silently wrong in the worst direction: it
    # describes FEWER models than exist, so every check consulting it passes
    # things it should catch. On 2026-08-26 it knew 5 of 14 models -- 9 real
    # tables were invisible to it, and nothing said so.
    #
    # A stale KL now fails loudly here. "app.models not importable" returns
    # UNKNOWN rather than a stale-artifact verdict, and is reported, not
    # silently passed.
    stale = schema_kl.assert_kl_fresh()
    if stale:
        unknown = [p for p in stale if p.startswith("UNKNOWN:")]
        if unknown:
            print(f"[schema-prm] KL freshness UNKNOWN: {unknown[0]}", file=sys.stderr)
        else:
            print("SCHEMA-KL IS STALE -- the committed knowledge layer does not "
                  "describe this tree:")
            for p in stale:
                print(f"  FAIL  {p}")
            print("\nRegenerate and commit:  python schema_kl.py --write")
            return 1

    kl = get_kl()

    # SQL-string referent pass -- REPORT-ONLY here, deliberately.
    #
    # Blocking for this class is armed at the EMISSION gate (goose_runner's
    # _schema_prm_gate), which is where a new emission is born and where
    # services/staged/circuit_breaker_status_api/contract.py got through on
    # 2026-08-25. Here it only reports, because this gate fires on any root
    # module a PR MODIFIES -- including the pre-August backlog. Blocking on a
    # backlog file an unrelated PR happens to touch is how a correct gate earns
    # itself an off switch. Flip this to blocking once the backlog is
    # quarantined; the counts printed below are the readout for that decision.
    sql_catalog, cat_reason = schema_kl.load_referent_catalog()
    if cat_reason:
        print(f"[schema-prm] SQL referent pass SKIPPED (report-only): {cat_reason}")

    bad = {}
    sql_report = {}
    for f in candidate_files():
        try:
            v = schema_kl.lint_file(f, kl)
        except OSError:
            continue
        if v:
            bad[f] = v
        if sql_catalog:
            try:
                sv = schema_kl.lint_file(f, kl, sql_catalog=sql_catalog)
            except OSError:
                continue
            extra = [x for x in sv if x not in v]
            if extra:
                sql_report[f] = extra

    if sql_report:
        n = sum(len(v) for v in sql_report.values())
        print(f"[schema-prm] SQL referent pass (REPORT-ONLY): {n} phantom table "
              f"reference(s) in bus-bound SQL across {len(sql_report)} file(s):")
        for f, vs in sql_report.items():
            for v in vs:
                print(f"  report-only  {f}  -- {v}")

    # #4076: split the verdict by scope. Root-level BLOCKS as it always has;
    # the newly-widened paths REPORT. Both are printed, with the counts, so the
    # report-only backlog is visible on every run and promoting it is one line.
    blocking = {f: v for f, v in bad.items() if is_blocking(f)}
    widened = {f: v for f, v in bad.items() if not is_blocking(f)}

    scanned = candidate_files()
    n_block = sum(1 for f in scanned if is_blocking(f))
    print(f"[schema-prm] scanned {len(scanned)} file(s): {n_block} blocking "
          f"(root-level), {len(scanned) - n_block} report-only (widened, #4076)")

    if widened:
        n = sum(len(v) for v in widened.values())
        print(f"[schema-prm] WIDENED SCOPE (REPORT-ONLY): {n} violation(s) "
              f"across {len(widened)} file(s) under {'/, '.join(WIDENED)}:")
        for f, vs in widened.items():
            for v in vs:
                print(f"  report-only  {f}  -- {v}")
        print("[schema-prm] These do NOT block. This gate is required, and "
              "turning newly-inspected paths red on the day they are first "
              "inspected blocks every open PR on an untriaged backlog. Promote "
              "them in BLOCKING_PATHS once this count is zero.")

    if blocking:
        print("SCHEMA-PRM violations (hallucinated schema vs the real app.models):")
        for f, vs in blocking.items():
            for v in vs:
                print(f"  FAIL  {f}  -- {v}")
        print("\nReference the real columns from app.models (see graphify-out/schema_kl.json).")
        return 1
    print("OK: no blocking schema violations in added/modified files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
