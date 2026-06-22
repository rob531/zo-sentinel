#!/usr/bin/env python3
"""Architect convergence hook (PreToolUse). After ARCHITECT_TOOL_BUDGET read/explore tool
calls in a session, DENY further reads so the architect must call propose_directive instead
of looping forever (the proven +0 root cause: goose-1.38 thrashes the read-heavy recipe and
times out at 480s with +0). propose_directive / propose_breaker_action are NEVER blocked.
Uses the Open-Plugins stdout-JSON deny (canary-proven on 1.38) so the reason is fed back and
the model adapts -> proposes. No matcher in hooks.json; this script does the filtering."""
import json, os, sys

BUDGET  = int(os.environ.get("ARCHITECT_TOOL_BUDGET", "12"))
PROPOSE = ("propose_directive", "propose_breaker_action")

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)                       # never crash the host tool

tool = str(payload.get("tool_name", ""))
sid  = str(payload.get("session_id", "s"))

# never block the GOAL -- proposing is always allowed
if any(t in tool for t in PROPOSE):
    sys.exit(0)

state = f"/tmp/architect_budget_{sid}.n"
try:
    n = int(open(state).read().strip())
except Exception:
    n = 0
n += 1
try:
    open(state, "w").write(str(n))
except Exception:
    pass

if n > BUDGET:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"Tool budget reached ({BUDGET} read/graph calls). STOP exploring and call "
            "propose_directive NOW with 1-3 net-new, well-specified directives from the "
            "context you already have. Do not call any more read/list/graph tools.")}}))
sys.exit(0)
