#!/usr/bin/env python3
"""PreToolUse tool-budget hook (canary). After HOOK_BUDGET calls of the matched tool in a
session, DENY further calls -- the architect analogue: force convergence to propose_directive
instead of looping on reads. Uses the Open-Plugins/Claude-Code stdout-JSON deny (feeds the
reason back so the model ADAPTS, vs exit-2 which can just make it stop). Logs every call so
the canary can prove whether THIS goose actually honored the deny."""
import json, os, sys

BUDGET = int(os.environ.get("HOOK_BUDGET", "2"))
LOG    = os.environ.get("HOOK_LOG", "/tmp/tool_budget.log")
try:
    payload = json.load(sys.stdin)
except Exception:
    payload = {}
sid  = str(payload.get("session_id", "nosession"))
tool = payload.get("tool_name", "?")
state = f"/tmp/tool_budget_{sid}.count"
try:
    n = int(open(state).read().strip())
except Exception:
    n = 0
n += 1
open(state, "w").write(str(n))
decision = "deny" if n > BUDGET else "allow"
with open(LOG, "a") as f:
    f.write(f"call {n} tool={tool} -> {decision}\n")
if decision == "deny":
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": f"Tool budget reached ({BUDGET}). Stop calling tools; finish now."}}))
sys.exit(0)
