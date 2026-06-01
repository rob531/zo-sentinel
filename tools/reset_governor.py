#!/usr/bin/env python3
"""reset_governor.py -- reset the activation governor's accumulated state.

Writes a fresh `ingestor_activation_state` row (empty content) via
write_service, so the governor's next `govern` cycle starts from a clean slate:
GovernorState.from_json("{}") defaults every field -> consecutive_green=0,
agreeing_artifacts=[], lifetime_false_promotes=0, activated=False.

Use AFTER the ingestor's grading has been corrected (e.g. the file-exists check
in evaluate()) so historical false-promotes don't permanently block arming
(lifetime_false_promotes == 0 is a hard activation gate).

Usage (on ZoComputer):
    python3 /home/workspace/zo_sentinel/tools/reset_governor.py
Then run a governor cycle to load the fresh state:
    PYTHONPATH=/home/workspace/zo_sentinel python3 -m zo_sentinel.ingestor govern
"""
from __future__ import annotations

import json
import sys
import urllib.request

WRITE_URL = "http://127.0.0.1:8772/write"
AGENT_ID = "zo_sentinel.activation_governor"
STATE_TYPE = "ingestor_activation_state"


def main() -> int:
    payload = {
        "table": "mesh_memory",
        "rows": [{
            "agent_id": AGENT_ID,
            "memory_type": STATE_TYPE,
            # empty dict -> GovernorState.from_json defaults all counters to zero
            "content": "{}",
            "importance": 0.5,
        }],
    }
    req = urllib.request.Request(
        WRITE_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10).read().decode()
    except Exception as e:
        print(f"reset failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(resp)
    print("governor state reset -- next `govern` cycle starts clean "
          "(consecutive_green=0, lifetime_false_promotes=0).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
