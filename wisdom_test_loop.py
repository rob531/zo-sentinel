#!/usr/bin/env python3
"""
wisdom_test_loop.py -- Short-interval wisdom synthesis test

Runs wisdom synthesis N times with INTERVAL_MINS between each run.
Purpose: verify tier logic, key health, and rate limiting across cycles.

Usage:
  python3 wisdom_test_loop.py              # 5 cycles, 3 min apart
  python3 wisdom_test_loop.py --cycles 3  # 3 cycles
  python3 wisdom_test_loop.py --mins 2    # 2 minute interval
"""
import sys, time, json, requests
from datetime import datetime, timezone

sys.path.insert(0, "/home/workspace/zo_mesh")
sys.path.insert(0, "/home/workspace/zo_sentinel")

from wisdom_synthesiser import synthesise_wisdom, write_sqlite_wisdom, write_wisdom_md

CYCLES       = 5
INTERVAL_MINS = 3
WRITE_SERVICE = "http://127.0.0.1:8772"

# Parse args
for i, arg in enumerate(sys.argv[1:]):
    if arg == "--cycles" and i+2 <= len(sys.argv)-1: CYCLES = int(sys.argv[i+2])
    if arg == "--mins"   and i+2 <= len(sys.argv)-1: INTERVAL_MINS = int(sys.argv[i+2])

print("=" * 70)
print(f"Wisdom Test Loop: {CYCLES} cycles, {INTERVAL_MINS}m apart")
print(f"Total duration: ~{CYCLES * INTERVAL_MINS} minutes")
print("Watching: tier used, latency, content quality")
print("=" * 70)

results = []

for cycle in range(1, CYCLES + 1):
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    print(f"\n[Cycle {cycle}/{CYCLES}] {now}")
    print("-" * 40)

    t0 = time.monotonic()
    wisdom = synthesise_wisdom()
    elapsed = int((time.monotonic() - t0) * 1000)

    if wisdom:
        write_sqlite_wisdom(wisdom)
        write_wisdom_md(wisdom)
        backend = wisdom.get("inference_backend", "?")
        preview = wisdom["wisdom"][:150].replace("\n", " ")
        print(f"  ✅ Backend:  {backend}")
        print(f"  ⏱  Elapsed: {elapsed}ms")
        print(f"  📝 Inputs:  {wisdom['inputs_used']}")
        print(f"  💡 Preview: {preview}...")
        results.append({"cycle": cycle, "status": "ok", "backend": backend,
                        "elapsed_ms": elapsed, "inputs": wisdom["inputs_used"]})

        # Write result to mesh_events for tracking
        try:
            requests.post(f"{WRITE_SERVICE}/write", json={
                "table": "mesh_events",
                "rows": {
                    "agent_id":   "wisdom_test_loop",
                    "event_type": "wisdom_test_cycle",
                    "tier":       "T4",
                    "payload":    json.dumps({"cycle": cycle, "backend": backend,
                                              "elapsed_ms": elapsed,
                                              "inputs": wisdom["inputs_used"]}),
                    "severity":   "INFO",
                    "created_at": datetime.now(timezone.utc).isoformat()
                }, "wait": True
            }, timeout=5)
        except Exception:
            pass
    else:
        print(f"  ❌ No wisdom generated (elapsed {elapsed}ms)")
        results.append({"cycle": cycle, "status": "failed", "elapsed_ms": elapsed})

    if cycle < CYCLES:
        print(f"  ⏳ Waiting {INTERVAL_MINS}m for next cycle...")
        time.sleep(INTERVAL_MINS * 60)

# Summary
print("\n" + "=" * 70)
print("TEST SUMMARY")
print("=" * 70)
ok      = [r for r in results if r["status"] == "ok"]
failed  = [r for r in results if r["status"] == "failed"]
backends = {}
for r in ok:
    b = r.get("backend", "?")
    backends[b] = backends.get(b, 0) + 1

print(f"  Cycles: {len(results)}  OK: {len(ok)}  Failed: {len(failed)}")
if ok:
    avg_ms = sum(r["elapsed_ms"] for r in ok) // len(ok)
    print(f"  Avg latency: {avg_ms}ms")
print(f"  Backends used:")
for b, count in backends.items():
    print(f"    {b}: {count}x")
if failed:
    print(f"  Failed cycles: {[r['cycle'] for r in failed]}")
print("=" * 70)
print("Check SYSTEM_WISDOM.md for latest output.")
print("Check zm log wisdom for escalation detail.")