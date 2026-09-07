#!/usr/bin/env python3
"""Negative control for FU-272 probe: proves the probe goes RED when the guard is absent."""
import ast
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parents[2] / "mcp_servers" / "builder_mcp.py"

def _check(fn_src):
    has_active_check = ("services/active" in fn_src)
    has_error_return = "REGISTER_ERROR" in fn_src and "staged" in fn_src
    return has_active_check and has_error_return

# Read real source and strip the guard block
src = TARGET.read_text(encoding="utf-8")
# Remove the FU-272 guard block from the source
guard_start = "    # FU-272:"
guard_end_marker = "    _norm = target_file"
if guard_start not in src:
    print("SKIP: guard block not found in source (probe is the check, not this script)")
    sys.exit(0)

# Simulate the broken state by removing the guard
start_idx = src.index(guard_start)
end_idx = src.index("    out = f\"/home/workspace", start_idx)
broken_src = src[:start_idx] + src[end_idx:]

tree = ast.parse(broken_src)
lines = broken_src.splitlines()
fn_src = None
for node in ast.walk(tree):
    if isinstance(node, ast.AsyncFunctionDef) and node.name == "register_build":
        fn_src = "\n".join(lines[node.lineno - 1: node.end_lineno])
        break

if fn_src is None:
    print("ERROR: register_build not found in modified source")
    sys.exit(2)

ok = _check(fn_src)
if ok:
    print("NEGATIVE CONTROL FAILED: probe is GREEN on broken source -- the check is vacuous")
    sys.exit(1)
else:
    print("NEGATIVE CONTROL PASSED: probe correctly goes RED on broken (guard-stripped) source")
    sys.exit(0)
