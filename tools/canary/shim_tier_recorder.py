#!/usr/bin/env python3
"""
Boot the REAL ladder_shim.py from the checkout with a RECORDING stub in place of
the paid ladder, so the goose-canary can drive a candidate goose through the
PRODUCTION TRANSPORT (:8796) instead of straight at a provider.

WHY THIS EXISTS (FU-119). Every step in goose-canary.yml points goose directly
at a provider. The mesh never takes that path: the architect and the builder
both point goose at ladder_shim.py on 127.0.0.1:8796, which hydrates keys,
rotates rungs and salvages malformed tool calls in escalation.py. So the gate
that decides whether a goose version may touch the runtime exercises a request
path the runtime does not use, and is structurally blind to the layer our scars
actually live in (scar #454, the TOOL:-as-prose salvage, the key hydrator).

WHY A STUB AND NOT A REAL RUNG. The objection that parked this tier for two
weeks was "CI would need provider keys as repo secrets", which is the
auth/secrets guardrail. It does not need them. The assertion this tier exists to
make -- did the namespaced tool name reach the shim byte-identical -- is about
the REQUEST goose sends, not the RESPONSE a provider returns. Recording the
request costs zero tokens and needs zero secrets. A tier that could only ever
run with a secret we are not authorised to add is a tier that would skip
forever, and a skip that reads as a pass is the failure class this repo has paid
for most.

WHAT IS STILL REAL, so nobody over-reads the green:
  * ladder_shim.py is the REAL file from the checkout -- its FastAPI routes, its
    pydantic request model, its message flattening and its SSE emitter all
    execute. If a goose version changes the shape of what it puts on the wire,
    this tier sees it.
  * escalation.py is IMPORTED for real (only `ask` is replaced). That alone
    catches the FU-158 class -- a module-level sys.exit / import-time side
    effect in the ladder is a mesh outage, and nothing else in CI imports it.
  * What is NOT tested here: rung rotation, key hydration, real provider
    behaviour. Those need keys. This tier does not claim them.

NEGATIVE CONTROL. --truncate-names N mangles the recorded tool names to N
characters, simulating goose 1.45 #10659 (overlong function names are now
truncated in provider requests). Our namespaced names are long
(`zo_directive_bridge__read_protected_files` is 41 chars), so a silent upstream
truncation would reproduce the 1.38 starvation shape from a cause no current
gate can see. The assertion must go RED under this flag or it is not measuring
anything.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECORD = "/tmp/shim_tier_requests.json"


def _install_recorder(record_path: Path, truncate: int | None) -> None:
    """Replace escalation.ask with a recorder. Import escalation FOR REAL first."""
    sys.path.insert(0, str(REPO_ROOT))

    try:
        import escalation  # noqa: F401  -- real import is itself an assertion
        print("SHIM_TIER::escalation_import=REAL", flush=True)
    except BaseException as exc:  # BaseException: a module-level SystemExit is the FU-158 shape
        print(f"SHIM_TIER::escalation_import=FAILED::{type(exc).__name__}: {exc}",
              flush=True)
        raise SystemExit(
            "escalation.py could not be imported. That is not a test-harness "
            "problem -- it is the ladder being unloadable, which on the tower "
            "is a mesh outage. Failing loudly rather than stubbing past it."
        )

    import escalation as esc

    class _Result:
        success = True
        text = "SHIM_TIER_STUB_OK"
        tool_calls = None
        attempts = 1
        error = None

    captured: list[dict] = []
    lock = threading.Lock()

    def _recording_ask(*, task_type=None, prompt="", system=None, max_tokens=None,
                       temperature=None, max_attempts=None, tools=None, **kw):
        names = []
        for t in (tools or []):
            # OpenAI tool shape: {"type":"function","function":{"name":...}}
            fn = t.get("function") if isinstance(t, dict) else None
            name = (fn or {}).get("name") if isinstance(fn, dict) else None
            if name is None and isinstance(t, dict):
                name = t.get("name")
            if name is None:
                continue
            if truncate is not None:
                name = name[:truncate]
            names.append(name)
        entry = {
            "task_type": task_type,
            "tool_count": len(tools or []),
            "tool_names": names,
            "prompt_chars": len(prompt or ""),
            "system_chars": len(system or ""),
        }
        with lock:
            captured.append(entry)
            record_path.write_text(json.dumps(captured, indent=2), encoding="utf-8")
        return _Result()

    esc.ask = _recording_ask

    # task_for_model must not explode on an unknown model id in CI.
    _orig_tfm = getattr(esc, "task_for_model", None)

    def _safe_task_for_model(model_id):
        try:
            return _orig_tfm(model_id) if _orig_tfm else "builder"
        except Exception:
            return "builder"

    esc.task_for_model = _safe_task_for_model


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--record", default=DEFAULT_RECORD)
    ap.add_argument("--truncate-names", type=int, default=None,
                    help="NEGATIVE CONTROL: truncate recorded tool names to N chars")
    args = ap.parse_args()

    record_path = Path(args.record)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text("[]", encoding="utf-8")

    if args.truncate_names is not None:
        print(f"SHIM_TIER::MODE=NEGATIVE_CONTROL truncate_names={args.truncate_names}",
              flush=True)
    else:
        print("SHIM_TIER::MODE=NORMAL", flush=True)

    _install_recorder(record_path, args.truncate_names)

    # The bridge defaults its root to the tower; a runner cannot write there.
    os.environ.setdefault("ZO_SENTINEL_DIR", "/tmp/zo_sentinel_shim_tier")

    import uvicorn
    import ladder_shim

    print(f"SHIM_TIER::booting ladder_shim v{ladder_shim.SHIM_VERSION} "
          f"on {args.host}:{args.port}", flush=True)
    uvicorn.run(ladder_shim.app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
