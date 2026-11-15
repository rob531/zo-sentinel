#!/usr/bin/env python3
"""Two-pole test for the CLASS-B AUTHORITY fold in fire_gate.py (FU-973).

NEGATIVE POLE (fails WITHOUT the fix): a Class-B target whose prod_deploy_class_b
clause is NOT positively cleared (BLOCKED, or UNKNOWN) must RESTAGE -- the fire path
must refuse to fire a migration on the irreversible edge without a positive clearance.
Before this fold, the fire path computed no class and consulted no clause, so this
case fired (the FU-182 "permission no code reads" hole).

POSITIVE CONTROL (stays green, proving the gate DISCRIMINATES and is not always-red):
a Class-B target that IS cleared (ALLOWED, rc=0), and ANY Class-A target, must leave
the verdict UNCHANGED. A gate that can only go red is a stall, not a gate.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fire_gate import apply_class  # noqa: E402

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'ok ' if ok else 'FAIL'} {name}: got {got!r} want {want!r}")
    if not ok:
        FAILS.append(name)


print("NEGATIVE POLE -- a Class-B deploy without a positive clearance must RESTAGE:")
check("classB_blocked_restages",
      apply_class("SAFE", {"klass": "B"}, {"rc": 1, "verdict": "BLOCKED"}),
      ("RESTAGE", True))
check("classB_unknown_restages",
      apply_class("SAFE", {"klass": "B"}, {"rc": 2, "verdict": "UNKNOWN"}),
      ("RESTAGE", True))
check("classB_unavailable_restages",
      apply_class("SAFE", {"klass": "B"}, {"rc": 2, "source": "unavailable"}),
      ("RESTAGE", True))

print("\nPOSITIVE CONTROL -- the gate must NOT fire on cleared / Class-A cases:")
check("classB_allowed_unchanged",
      apply_class("SAFE", {"klass": "B"}, {"rc": 0, "verdict": "ALLOWED"}),
      ("SAFE", False))
check("classA_unchanged",
      apply_class("SAFE", {"klass": "A"}, {"verdict": "NOT-ENGAGED"}),
      ("SAFE", False))
check("classA_even_if_authority_blocked_unchanged",
      apply_class("SAFE", {"klass": "A"}, {"rc": 1}),
      ("SAFE", False))
check("class_unknown_does_not_restage",
      apply_class("SAFE", {"klass": "UNKNOWN"}, {"rc": 2}),
      ("SAFE", False))

print("\nIDEMPOTENCE -- an already-RESTAGE verdict is not re-forced by this fold:")
check("already_restage_not_double_forced",
      apply_class("RESTAGE", {"klass": "B"}, {"rc": 1}),
      ("RESTAGE", False))

print()
if FAILS:
    print(f"TWO-POLE FAILED: {len(FAILS)} case(s): {FAILS}")
    sys.exit(1)
print("TWO-POLE PASSED: negative pole RESTAGEs, positive control stays green.")
sys.exit(0)
