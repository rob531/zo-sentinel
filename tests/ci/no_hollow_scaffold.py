#!/usr/bin/env python3
"""Anti-hollow-scaffold gate. Fails a PR that ADDS a root-level Python module
which is a hollow scaffold: a standalone FastAPI app/router that does not use the
real data layer (app.db/app.models), or one that ships a mock/placeholder DB.
Catches the ladder's recurring "passes CI but is fake" output. Real modules must
import the app data layer and use no mock DB (mirror verdict_breakdown_api.py)."""
import os
import re
import subprocess
import sys

BASE = os.environ.get("BASE_REF", "origin/main")
SKIP = ("app/", "tests/", "zo_sentinel/", "scripts/", "migrations/", "infra/",
        "goose_recipes/", ".github/", "_scratch/", "archive/", "quarantine/")
MOCK = re.compile(r"class\s+Mock|MockDB|mock database|mock data|placeholder|dummy data|"
                  r"simulate fetching|in-memory (db|database)|# *Mock", re.I)
BUILDS_API = re.compile(r"FastAPI\(|APIRouter\(|@app\.(get|post)|@router\.(get|post)")
REAL = re.compile(r"from app\.db|from app\.models|import app\.db|app\.models import|"
                  r"get_session|from app import|import verdict_breakdown_api")


def added_files():
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=A", f"{BASE}...HEAD"],
                       capture_output=True, text=True)
    return [f.strip() for f in r.stdout.splitlines() if f.strip()]


def main():
    bad = []
    for f in added_files():
        if "/" in f or not f.endswith(".py"):   # root-level single-file modules only
            continue
        if any(f.startswith(d) for d in SKIP):
            continue
        try:
            src = open(f, encoding="utf-8").read()
        except OSError:
            continue
        if MOCK.search(src):
            bad.append((f, "mock/placeholder DB"))
        elif BUILDS_API.search(src) and not REAL.search(src):
            bad.append((f, "standalone API with no real data layer (app.db/app.models)"))
    if bad:
        print("HOLLOW SCAFFOLD(S) DETECTED -- added modules that are not real:")
        for f, why in bad:
            print(f"  FAIL  {f}  -- {why}")
        print("\nReal modules import the app data layer (app.db/app.models) and use no mock DB.")
        return 1
    print("OK: no hollow scaffolds among added root-level modules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
