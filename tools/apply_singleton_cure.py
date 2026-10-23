#!/usr/bin/env python3
"""Route daemons' single-instance check through singleton_lock, and make
daemon_wrapper.sh refuse to stack duplicate wrappers.

Idempotent by construction: re-running is a no-op and reports it as such.
Surgical by construction: it replaces only the `check_single_instance` block
and adds one import, so it can be applied to a LIVE file that has drifted from
its repo copy without clobbering the drift. That matters -- on 2026-09-21 the
live /home/workspace/zo_mesh/daemon_wrapper.sh and the repo
tools/daemon_wrapper.sh had already diverged (the repo has a RUN_MODE=module
fork the live file lacks), so "deploy by copying the repo file over" would
have silently reverted live behaviour.

    # repo tree
    python3 tools/apply_singleton_cure.py .

    # live box (daemons in one dir, wrapper somewhere else)
    python3 tools/apply_singleton_cure.py /home/workspace/zo_sentinel \\
        --wrapper /home/workspace/zo_mesh/daemon_wrapper.sh

    # census only, change nothing
    python3 tools/apply_singleton_cure.py . --report

Background: gh#5412. See singleton_lock.py for the root cause.
"""

import argparse
import os
import re
import shutil
import sys
import time

# The live daemon roster INTERSECT the identity-free os.kill(pid, 0) shape.
# Resolved 2026-09-21 from `ps -eo cmd | grep daemon_wrapper.sh` on the live
# box (the runtime is the oracle, per HARNESS_DOCTRINE R1), not from a list
# someone maintained by hand.
LIVE_DEFECTIVE = [
    "attestation_engine.py",
    "signal_bridge.py",
    "mcp_scanner.py",
    "registry_promoter_daemon.py",
    "fingerprint_runner_daemon_v3.py",
    "threat_intel_ingestor.py",
    "discovery_npm_paginator.py",
    "ecosystems_metadata_fetcher.py",
    "goose_runner.py",
]

DEFECT_RE = re.compile(r"os\.kill\([A-Za-z_]+,\s*0\)")

DELEGATION = '''def check_single_instance(*_args, **_kwargs):
    """Single-instance lock, identity-verified. See singleton_lock.py.

    Was: os.kill(pid, 0) -- "does SOME process own this number?" That let a
    recycled PID wedge this daemon shut permanently (2026-09-21, gh#5412).
    """
    _svc = globals().get("SERVICE_NAME") or os.path.splitext(
        os.path.basename(__file__))[0]
    return singleton_lock.check_single_instance(_svc, script=__file__)
'''

IMPORT_LINE = "import singleton_lock  # identity-verified single-instance lock\n"

WRAPPER_GUARD = '''# --- single-wrapper guard (2026-09-21, gh#5412) --------------------------
# Seven independent spawners call this script (go.sh, watchdog.sh,
# watchdog_daemon.py, liveness_probe.py, zo_sentinel_builder.py,
# interval_runner.sh, resurrect_sentinel_daemons.sh). Each one that saw
# attestation_engine "down" added ANOTHER respawn loop, so 19 wrappers
# accumulated against a daemon that could not start. flock is the right
# primitive here precisely because it CANNOT go stale: the kernel drops it
# when the holder dies, so unlike a pidfile it never needs a liveness check
# that PID recycling can fool.
# Escape hatch for tests/negative controls only: ZO_WRAPPER_ALLOW_DUPLICATE=1
if [[ "${ZO_WRAPPER_ALLOW_DUPLICATE:-0}" != "1" ]] && command -v flock >/dev/null 2>&1; then
  WRAPPER_LOCK_DIR="/var/run/zo"
  mkdir -p "$WRAPPER_LOCK_DIR" 2>/dev/null || WRAPPER_LOCK_DIR="/tmp"
  WRAPPER_LOCK="${WRAPPER_LOCK_DIR}/wrapper_${NAME}.lock"
  exec 9>"$WRAPPER_LOCK" 2>/dev/null || true
  if ! flock -n 9 2>/dev/null; then
    log "a wrapper for ${NAME} already holds ${WRAPPER_LOCK}; not stacking another (rc=0)"
    exit 0
  fi
fi
# --- end single-wrapper guard -------------------------------------------

'''

GUARD_NEEDLE = "single-wrapper guard (2026-09-21, gh#5412)"

# The guard must sit ABOVE the RUN_MODE fork so it covers module mode too.
# Anchoring on the script-mode log line put it inside the else-branch on the
# first attempt -- the live wrapper has no RUN_MODE fork, the repo copy does,
# and patching the shape you read on the box into the file in the repo is
# exactly HARNESS_DOCTRINE's headline class.
WRAPPER_ANCHORS = (
    'if [[ "$RUN_MODE" == "module" ]]; then',
    'log "wrapper starting for $SCRIPT',
    'log "wrapper starting',
)


def backup(path):
    dest = "%s.pre-c125.%s" % (path, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    shutil.copy2(path, dest)
    return dest


def block_end(lines, start):
    """Index just past the def block beginning at `start`."""
    i = start + 1
    while i < len(lines):
        if lines[i].strip() and not lines[i][:1].isspace():
            return i
        i += 1
    return len(lines)


def patch_daemon(path, dry=False):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    if "singleton_lock.check_single_instance" in text:
        return "already"
    if not DEFECT_RE.search(text):
        return "not-defective"

    lines = text.splitlines(keepends=True)
    idx = next(
        (i for i, l in enumerate(lines) if re.match(r"^def check_single_instance\b", l)),
        None,
    )
    if idx is None:
        return "no-def"
    if dry:
        return "would-patch"

    lines[idx:block_end(lines, idx)] = [DELEGATION, "\n"]

    last_import = 0
    for i, l in enumerate(lines[:80]):
        if re.match(r"^(import |from )\S", l):
            last_import = i
    lines.insert(last_import + 1, IMPORT_LINE)
    if not re.search(r"^import os\b", text, re.M):
        lines.insert(0, "import os\n")

    backup(path)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("".join(lines))
    return "patched"


def patch_wrapper(path, dry=False):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if GUARD_NEEDLE in text:
        return "already"

    i = -1
    for anchor in WRAPPER_ANCHORS:
        i = text.find(anchor)
        if i != -1:
            break
    if i == -1:
        return "no-anchor"
    if dry:
        return "would-patch"

    backup(path)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text[:i] + WRAPPER_GUARD + text[i:])
    return "patched"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--wrapper", default=None,
                    help="path to daemon_wrapper.sh (default <root>/tools/daemon_wrapper.sh)")
    ap.add_argument("--report", action="store_true", help="change nothing")
    args = ap.parse_args()

    results, missing = {}, []
    for name in LIVE_DEFECTIVE:
        p = os.path.join(args.root, name)
        if os.path.exists(p):
            results[name] = patch_daemon(p, dry=args.report)
        else:
            missing.append(name)

    wrapper = args.wrapper or os.path.join(args.root, "tools", "daemon_wrapper.sh")
    if os.path.exists(wrapper):
        results[wrapper] = patch_wrapper(wrapper, dry=args.report)
    else:
        missing.append(wrapper)

    for k, v in sorted(results.items()):
        print("%-52s %s" % (k, v))
    if missing:
        print("\nNOT PRESENT IN THIS TREE (%d):" % len(missing))
        for m in missing:
            print("  " + m)

    tally = {}
    for v in results.values():
        tally[v] = tally.get(v, 0) + 1
    print("\n" + "  ".join("%s=%d" % kv for kv in sorted(tally.items()))
          + "  missing=%d" % len(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
