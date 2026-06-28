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


def candidate_files():
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=AM", f"{BASE}...HEAD"],
                       capture_output=True, text=True)
    out = []
    for f in r.stdout.splitlines():
        f = f.strip()
        if not f or not f.endswith(".py"):
            continue
        if "/" in f:                      # root-level single-file modules only
            continue
        if any(f.startswith(d) for d in SKIP):
            continue
        out.append(f)
    return out


def get_kl():
    """Prefer the live introspection of app.models (ground truth); fall back to the
    committed KL artifact if the app isn't importable in this environment."""
    try:
        return schema_kl.build_schema_kl()
    except Exception as e:
        print(f"[schema-prm] build_schema_kl failed ({e}); using committed KL", file=sys.stderr)
        return schema_kl.load_schema_kl()


def main():
    kl = get_kl()
    bad = {}
    for f in candidate_files():
        try:
            v = schema_kl.lint_file(f, kl)
        except OSError:
            continue
        if v:
            bad[f] = v
    if bad:
        print("SCHEMA-PRM violations (hallucinated schema vs the real app.models):")
        for f, vs in bad.items():
            for v in vs:
                print(f"  FAIL  {f}  -- {v}")
        print("\nReference the real columns from app.models (see graphify-out/schema_kl.json).")
        return 1
    print("OK: no schema violations in added/modified root-level modules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
