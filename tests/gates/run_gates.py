#!/usr/bin/env python3
"""
run_gates.py -- Orchestrator for gate tests.

Usage:
    python3 run_gates.py              # run all gates
    python3 run_gates.py 1            # run gate 1 only
    python3 run_gates.py 2 5 7        # run gates 2, 5, 7
    python3 run_gates.py --list       # show available gates

Exit codes:
    0 -- all gates passed
    1 -- one or more gates failed
    2 -- infra error (gate_errors.db unreachable, etc.)
"""
import sys
import time

sys.path.insert(0, "/home/workspace/zo_sentinel/tests/gates")
from gate_framework import gate_run, INTER_GATE_SEC
from gate_1_infrastructure   import Gate1Infrastructure
from gate_2_schema_contracts import Gate2SchemaContracts
from gate_5_synthesis_flow   import Gate5SynthesisFlow
from gate_7_threat_flow      import Gate7ThreatFlow
from gate_8_new_module      import Gate8NewModule
from gate_9_signal_diversity import Gate9SignalDiversity

GATES = {
    1: ("infrastructure",    Gate1Infrastructure),
    2: ("schema_contracts",  Gate2SchemaContracts),
    5: ("synthesis_flow",    Gate5SynthesisFlow),
    7: ("threat_flow",       Gate7ThreatFlow),
    8: ("new_module",        Gate8NewModule),
    9: ("signal_diversity",  Gate9SignalDiversity),
}

# Recommended execution order:
#   1 first -- no point running the rest if the infra is down
#   2 next  -- static checks, fast, finds schema drift early
#   5 then  -- synthesis flow depends on schema being correct (Gate 2)
#   7 next  -- threat flow depends on world_articles + registry (Gate 1)
#   8 last  -- new module smoke needs mesh_memory via write_service (Gate 1 must pass)
DEFAULT_ORDER = [1, 2, 5, 7, 8, 9]


def main():
    args = sys.argv[1:]
    if "--list" in args:
        print("Available gates:")
        for gid, (name, _) in sorted(GATES.items()):
            print(f"  {gid}: {name}")
        return 0

    if args:
        try:
            selected = [int(a) for a in args]
        except ValueError:
            print(f"Unrecognised arg: {args}")
            return 2
    else:
        selected = [g for g in DEFAULT_ORDER if g in GATES]

    unknown = [g for g in selected if g not in GATES]
    if unknown:
        print(f"Unknown gates: {unknown}")
        print("Use --list to see available")
        return 2

    trigger = "manual" if sys.stdin.isatty() else "automated"

    print(f"\n{'='*60}")
    print(f"Gate orchestrator -- running {len(selected)} gate(s): {selected}")
    print(f"{'='*60}")

    with gate_run(trigger=trigger) as (db, run_id):
        total_failures = 0
        for i, gate_id in enumerate(selected):
            name, cls = GATES[gate_id]
            if i > 0:
                time.sleep(INTER_GATE_SEC)
            gate = cls(db, run_id)
            gate.run()
            total_failures += gate.failures

    return 1 if total_failures else 0


if __name__ == "__main__":
    sys.exit(main())