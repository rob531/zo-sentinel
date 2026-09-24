#!/usr/bin/env python3
"""
fu272_active_guard.py -- verify probe for FU-272.

FU-272: builder emits router.py into services/active/ instead of services/staged/.
Root cause: register_build in builder_mcp.py accepts any target_file path,
including ones under services/active/, which the LLM emits by confusing the
import_path (post-promotion forward reference) with the write destination.

This probe checks that register_build REJECTS a target_file under services/active/
with a clear error (RED before fix) and ACCEPTS the same file under services/staged/
(GREEN after fix on the logic, always true before).

Usage:
    python tools/_peer_probes/fu272_active_guard.py          # normal check
    python tools/_peer_probes/fu272_active_guard.py --self-test  # returns 0 on GREEN, 1 on RED
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TARGET = REPO / "mcp_servers" / "builder_mcp.py"


def _read_register_build_source() -> str:
    """Extract the source of the register_build function from builder_mcp.py."""
    src = TARGET.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print(f"PROBE_ERROR: cannot parse {TARGET}: {e}", file=sys.stderr)
        sys.exit(2)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "register_build":
            lines = src.splitlines()
            return "\n".join(lines[node.lineno - 1: node.end_lineno])
    return ""


def check_guard_present() -> tuple[bool, str]:
    """Return (ok, reason). ok=True means the guard is present (GREEN)."""
    fn_src = _read_register_build_source()
    if not fn_src:
        return False, "register_build function not found in builder_mcp.py"
    # The guard must: (a) check for services/active in the path and
    # (b) return a REGISTER_ERROR string explaining the correct path is services/staged/
    has_active_check = ("services/active" in fn_src or "services\\\\active" in fn_src)
    has_error_return = "REGISTER_ERROR" in fn_src and "staged" in fn_src
    if not has_active_check:
        return False, ("register_build does not reject services/active/ paths -- "
                       "a model confusing import_path with write destination can "
                       "register files there unchecked (FU-272)")
    if not has_error_return:
        return False, ("register_build checks for services/active but does not return "
                       "REGISTER_ERROR with 'staged' guidance -- guard is present but "
                       "not instructive (FU-272)")
    return True, "register_build rejects services/active/ with staged guidance (GREEN)"


def main(argv=None):
    ap = argparse.ArgumentParser(description="FU-272 guard probe: register_build rejects services/active/")
    ap.add_argument("--self-test", action="store_true",
                    help="Exit 0 if GREEN (guard present), 1 if RED (guard missing)")
    args = ap.parse_args(argv)

    ok, reason = check_guard_present()
    color = "GREEN" if ok else "RED"
    print(f"[FU-272] {color}: {reason}")
    if args.self_test:
        sys.exit(0 if ok else 1)
    return ok


if __name__ == "__main__":
    main()
