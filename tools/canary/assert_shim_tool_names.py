#!/usr/bin/env python3
"""
Assert that the namespaced tool name goose put on the wire reached the ladder
shim BYTE-IDENTICAL to the name the bridge registered.

This is the one assertion the direct-to-provider canary tier structurally cannot
make. goose 1.45 shipped #10659 -- overlong function names are now TRUNCATED in
provider requests. Our names are namespaced and long:

    zo_directive_bridge__read_protected_files     (41 chars)

A silent truncation reproduces the 1.38 architect starvation exactly: the agent
emits a well-formed call, the bridge never receives it, and every surface that
measures process liveness reports the architect healthy while it proposes +0.
The existing namespacing probe covers PRESENCE of the call. It does not and
cannot cover NAME LENGTH ON THE WIRE, because on the direct path we never see
the wire.

Emits one machine-readable verdict line per assertion so a run can be adjudicated
without opening the log.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The verbatim name directive_architect.yaml hardcodes. If this constant and the
# recipe ever disagree, THIS is the copy that is wrong -- the recipe is the
# contract with the running architect.
EXPECTED = "zo_directive_bridge__read_protected_files"


def verdict(name: str, ok: bool, detail: str = "") -> bool:
    print(f"SHIM_TIER_VERDICT::{name}::{'PASS' if ok else 'FAIL'}"
          + (f"::{detail}" if detail else ""), flush=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", default="/tmp/shim_tier_requests.json")
    ap.add_argument("--expect", default=EXPECTED)
    args = ap.parse_args()

    path = Path(args.record)
    if not path.exists():
        verdict("record_present", False, "no record file -- the shim was never reached")
        return 1

    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        verdict("record_parses", False, f"{type(exc).__name__}: {exc}")
        return 1

    ok = True

    # (1) The shim was reached at all. Distinguishing this from (2) matters: a
    #     zero-entry record means the TRANSPORT failed, not that a name changed.
    #     Never let one bucket absorb the other (the FU-108 lesson).
    ok &= verdict("shim_reached", bool(entries), f"requests={len(entries)}")
    if not entries:
        print("The shim recorded zero requests. goose never sent anything to "
              ":8796 -- treat this as a TRANSPORT failure, not a rename.")
        return 1

    # (2) goose advertised tools at all. If this is zero the bridge did not load,
    #     which is the FU-117 shape (goose warns and continues without it).
    all_names = [n for e in entries for n in e.get("tool_names", [])]
    ok &= verdict("tools_advertised", bool(all_names),
                  f"distinct={len(set(all_names))}")

    # (3) THE POINT OF THE TIER: the namespaced name arrived untruncated.
    exact = args.expect in all_names
    ok &= verdict("name_untruncated", exact, f"expect={args.expect}")

    if not exact:
        prefixes = sorted({n for n in set(all_names)
                           if args.expect.startswith(n) and n != args.expect})
        if prefixes:
            print(f"TRUNCATION DETECTED. Expected {len(args.expect)} chars "
                  f"'{args.expect}', wire carried prefixes: {prefixes}")
            print("This is upstream #10659 biting our namespaced names. It would "
                  "present on the tower as an architect at +0 with no error.")
        else:
            print(f"Name absent and not a prefix -- a genuine RENAME, not a "
                  f"truncation. Names seen: {sorted(set(all_names))[:40]}")

    print(f"SHIM_TIER_VERDICT::overall::{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
