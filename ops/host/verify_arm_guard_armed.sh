#!/usr/bin/env bash
# verify_arm_guard_armed.sh -- the host-side subscriber for arm_guard.sh's evidence.
#
# WHY THIS FILE EXISTS (improvement-loop cycle-0225, FU-572)
#   ops/host/arm_guard.sh gives ARMING a trigger (cycle-0160). Its evidence tool,
#   tools/c160_arm_guard_evidence.py, carries FIVE controls. FOUR are bash/static
#   and already run in CI through tests/test_c160_arm_guard.py. The FIFTH --
#   "the-guard-has-a-call-site" -- reads the LIVE host crontab (R1) and has no
#   meaning on a CI runner, so the test suite deliberately omits it. Both that
#   tool's docstring and the test's say that control "is run on the host".
#
#   Nothing on the host ran it. The subscriber those two docstrings named was
#   never built, so tools/c160_arm_guard_evidence.py scanned DARK -- consulted by
#   nothing, the exact lane_halt.py shape dark_tools.py exists to catch: a caveat
#   that predicted its own fate in writing and had no subscriber. "A caveat in a
#   docstring has no subscriber." This file is that subscriber.
#
# WHAT THIS DOES
#   Runs the full evidence suite on the host -- where bash is real and crontab
#   exists -- so ALL FIVE controls actually measure, including the live call-site
#   one the CI suite cannot. It adds NO judgement of its own and mutates nothing:
#   it is a read-only verifier that passes through the evidence tool's verdict.
#   THIS IS NOT A GATE. It blocks nothing, fails no build, refuses no PR.
#
#     on demand   -- bash ops/host/verify_arm_guard_armed.sh [repo_dir]
#   It mirrors ops/host/verify_escalation_armed.sh: a host verifier invoked on
#   demand (or by a watchdog). A cadence is optional -- an hourly cron line
#   alongside zo_arm_guard would make a vanished call-site visible as a verdict
#   that went red, but that is a separate host-config change, not this file.
#
# Exit: passes through tools/c160_arm_guard_evidence.py --controls --
#   0  all controls green, every mutant observed red
#   1  a control failed
#   2  the evidence tool is missing (cannot verify)
#   4  a control could not be measured here (NOT a pass -- run on the zo host)
# Usage: bash ops/host/verify_arm_guard_armed.sh [repo_dir]
set -uo pipefail

REPO="${ZO_REPO:-/home/workspace/zo_sentinel}"
[ "$#" -ge 1 ] && REPO="$1"

log() { echo "[verify-arm-guard] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

# Invoked by its LITERAL tracked path, not via a variable: dark_tools.py matches
# invocation SHAPE by the literal name, so a "$VAR"-only call would leave the
# tool scanning dark even while this script runs it every time (the trap that
# makes a wrapper a Potemkin cure). The path stays $REPO-relative so the runner
# works against any checkout, host or CI.
if [ ! -f "$REPO/tools/c160_arm_guard_evidence.py" ]; then
  log "FATAL: $REPO/tools/c160_arm_guard_evidence.py missing -- cannot verify arm_guard.sh"
  exit 2
fi

log "running arm_guard evidence controls on the host (bash + crontab real here)"
python3 "$REPO/tools/c160_arm_guard_evidence.py" --controls
rc=$?
log "evidence controls rc=$rc"
exit "$rc"
