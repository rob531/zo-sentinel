#!/usr/bin/env python3
"""Two-pole offline test for fire_gate.py --decouple-class-a + decoupled_release_plan.py.

Covers:
  - Pole B (RED / fail-closed): coupled model change + DROP migration, OR missing/error planner -> RESTAGE.
  - Pole A (GREEN): uncoupled Class-B delta (like 0016 on bf30e09d..2756a1f3) -> decoupled Class-A allowed.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fire_gate import apply_decouple, apply_class  # noqa: E402

PLANNER = pathlib.Path(r"D:\zo\Zocomputer Agents\_tools\decoupled_release_plan.py")
PROD_DRIFT = pathlib.Path(r"D:\zo\_lanes\prod-drift")

FAILS = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(name)


print("POLE B (fail-closed) -- coupled / RED / planner error must REFUSE decouple and RESTAGE:")
v, dec, rst, _ = apply_decouple("SAFE", {"klass": "B"}, {"class_a_decouple": {"eligible": False, "blockers": ["MODEL_COLUMN_OR_TABLE_COUPLING(1)"]}}, requested=True)
check("coupled_refused", (v, dec, rst), ("RESTAGE", False, True))

v, dec, rst, _ = apply_decouple("SAFE", {"klass": "B"}, {"error": "planner failed", "class_a_decouple": {"eligible": True}}, requested=True)
check("planner_error_refused", (v, dec, rst), ("RESTAGE", False, True))

v, dec, rst, _ = apply_decouple("SAFE", {"klass": "B"}, {"class_a_decouple": {"eligible": False}}, requested=False)
check("not_requested_noop", (v, dec, rst), ("SAFE", False, False))

print("\nPOLE A -- uncoupled eligible=True decouples cleanly without touching default apply_class:")
v, dec, rst, _ = apply_decouple("SAFE", {"klass": "B"}, {"class_a_decouple": {"eligible": True, "blockers": []}}, requested=True)
check("eligible_decouples", (v, dec, rst), ("SAFE", True, False))
check("decoupled_auth_allows_apply_class", apply_class(v, {"klass": "B"}, {"verdict": "DECOUPLED-TO-CLASS-A", "rc": 0}), ("SAFE", False))
check("apply_class_unchanged_without_decouple", apply_class("SAFE", {"klass": "B"}, {"rc": 1}), ("RESTAGE", True))

if PLANNER.is_file():
    print("\nLIVE PLANNER -- bf30e09d..2756a1f3 + self-test:")
    st = subprocess.run([sys.executable, str(PLANNER), "--self-test"], capture_output=True, text=True)
    check("planner_selftest_rc0", st.returncode, 0)
    if PROD_DRIFT.is_dir():
        lp = subprocess.run(
            [sys.executable, str(PLANNER), "--repo", str(PROD_DRIFT),
             "--prod-sha", "bf30e09da3d301f0d3256a3b86600ffa83660a66",
             "--target-sha", "2756a1f3f653713228f847bb0814a550985b4621", "--json"],
            capture_output=True, text=True,
        )
        check("live_delta_rc0", lp.returncode, 0)
        data = json.loads(lp.stdout)
        check("live_delta_decouple_eligible", data["class_a_decouple"]["eligible"], True)
        check("live_delta_content_class", data["overall_migration_content_class"], "GREEN")

if FAILS:
    print(f"\nFAILED: {FAILS}")
    raise SystemExit(1)
print("\nTWO_POLE_DECOUPLE_PASS")
