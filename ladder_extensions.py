#!/usr/bin/env python3
"""
ladder_extensions.py -- v1 (2026-04-28). Optional side-effect module that
extends escalation.LADDER with two free Gemma rungs (RcGeminiAPIKey path,
unlimited TPM per April 22 research) and adds TASK_MAX_ATTEMPTS overrides
so wisdom_synthesis and similar high-quality tasks can traverse the full
ladder when starting from a paid tier that 402's. To activate: ensure
this module is importable from PYTHONPATH (it sits next to escalation.py
in /home/workspace/zo_sentinel/) and that escalation.py imports it. Until
escalation.py contains an import block for it, this module is dormant.
"""

import escalation
from escalation import ModelSpec

EXTRA_RUNGS = [
    ModelSpec("gemini_direct", "gemma-4-31b-it",  128_000, 15, 0.0,
              "Gemma 4 31B (free via AI Studio, 15 RPM, UNLIMITED TPM)"),
    ModelSpec("gemini_direct", "gemma-4-26b-it",  128_000, 15, 0.0,
              "Gemma 4 26B (free via AI Studio, 15 RPM, UNLIMITED TPM)"),
]

TASK_MAX_ATTEMPTS = {
    "wisdom_synthesis": 13,
    "directive_gen":    8,
    "builder":          8,
}

# Append new rungs to the live ladder. Idempotent guard: check by model_id
# so re-import (e.g. via importlib.reload) does not duplicate rungs.
_existing_ids = {s.model_id for s in escalation.LADDER}
for rung in EXTRA_RUNGS:
    if rung.model_id not in _existing_ids:
        escalation.LADDER.append(rung)

# Monkey-patch escalation.ask to consult TASK_MAX_ATTEMPTS when caller did
# not pass max_attempts explicitly. Preserves original ask via closure.
_orig_ask = escalation.ask
def _patched_ask(task_type, prompt, system=None, max_tokens=4096,
                 temperature=0.5, max_attempts=None, tools=None):
    if max_attempts is None:
        max_attempts = TASK_MAX_ATTEMPTS.get(task_type, 4)
    return _orig_ask(task_type, prompt, system=system, max_tokens=max_tokens,
                     temperature=temperature, max_attempts=max_attempts,
                     tools=tools)
escalation.ask = _patched_ask

if __name__ == "__main__":
    print("ladder_extensions.py loaded")
    print(f"  Added rungs: {[r.model_id for r in EXTRA_RUNGS]}")
    print(f"  TASK_MAX_ATTEMPTS: {TASK_MAX_ATTEMPTS}")
    print(f"  escalation.LADDER now has {len(escalation.LADDER)} rungs")