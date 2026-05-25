#!/usr/bin/env python3
"""
r1_wire_v3_enrichments_patcher.py
Idempotent patcher that rewires signal_analyser.py to use v3/v4 enrichment modules
with fallback to earlier versions on ImportError/AttributeError.
"""
import argparse
import os
import re
import shutil
import sys
import traceback
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIGNAL_ANALYSER_PATH = os.path.join(SCRIPT_DIR, "signal_analyser.py")
MARKER = "# _zo_v3_wire_v1"

# Enrichment modules in probe-order: v4 -> v3 -> v2 -> base
ENRICHMENTS = [
    "tool_description_safety_enrichment",
    "permission_scope_enrichment",
    "temporal_stability_enrichment",
    "community_signal_enrichment",
    "supply_chain_enrichment",
]

VERSION_SUFFIXES = ["_v4", "_v3", "_v2", ""]


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_source(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_source(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def backup_path(path):
    ts = utc_now_iso()
    return f"{path}.bak.{ts}Z"


def already_wired(content):
    return MARKER in content


def find_import_line_for_enrichment(content, base_name):
    """Find 'from X import Y' line for a given enrichment base name."""
    for suffix in VERSION_SUFFIXES:
        module_name = f"{base_name}{suffix}"
        pattern = rf"^from {re.escape(module_name)} import .+?$"
        m = re.search(pattern, content, re.MULTILINE)
        if m:
            return m.group(0), module_name
    return None, None


def build_try_except_import(enrichment, selected_module):
    """Build a try/except block that imports from selected module with fallback."""
    base_name = enrichment
    lines = []
    lines.append("")
    lines.append(f"# _zo_v3_wire_v1: {enrichment} -> {selected_module}")
    
    for suffix in VERSION_SUFFIXES:
        mod = f"{base_name}{suffix}"
        lines.append(f"try:")
        lines.append(f"    from {mod} import score as {enrichment}_score, compute_score as {enrichment}_compute_score, get_score_band as {enrichment}_get_score_band")
        lines.append(f"    {enrichment}_module = '{mod}'")
        lines.append(f"    break")
        lines.append(f"except (ImportError, AttributeError):")
        if suffix == "":
            lines.append(f"    import {enrichment}")
            lines.append(f"    {enrichment}_module = '{enrichment}'")
        else:
            lines.append(f"    pass")

    return "\n".join(lines)


def generate_replacement(enrichment, current_import_line):
    """Generate replacement try/except block for an enrichment."""
    selected_module = None
    for suffix in VERSION_SUFFIXES:
        mod = f"{enrichment}{suffix}"
        try:
            __import__(mod)
            selected_module = mod
            break
        except ImportError:
            continue
    
    if selected_module is None:
        selected_module = enrichment
    
    block = build_try_except_import(enrichment, selected_module)
    return block, selected_module


def analyze_imports(content):
    """Analyze current imports and determine what needs to change."""
    decisions = {}
    
    for enrichment in ENRICHMENTS:
        current_line, current_module = find_import_line_for_enrichment(content, enrichment)
        if current_line is None:
            decisions[enrichment] = {
                "current": None,
                "selected": None,
                "status": "NOT_FOUND",
                "line": None
            }
            continue
        
        selected_module = None
        for suffix in VERSION_SUFFIXES:
            mod = f"{enrichment}{suffix}"
            try:
                __import__(mod)
                selected_module = mod
                break
            except ImportError:
                continue
        
        if selected_module is None:
            selected_module = enrichment
        
        current_is_latest = current_module == selected_module
        decisions[enrichment] = {
            "current": current_module,
            "selected": selected_module,
            "status": "CURRENT" if current_is_latest else "OUTDATED",
            "line": current_line
        }
    
    return decisions


def patch_content(content, decisions):
    """Apply patches to content."""
    patched = content
    offset = 0
    
    replacements = []
    
    for enrichment, decision in decisions.items():
        if decision["status"] == "NOT_FOUND":
            continue
        if decision["status"] == "CURRENT":
            continue
        
        current_line = decision["line"]
        new_block = build_try_except_import(enrichment, decision["selected"])
        
        replacements.append((current_line, new_block))
    
    for old_line, new_block in replacements:
        idx = patched.find(old_line)
        if idx >= 0:
            patched = patched[:idx] + new_block + "\n" + patched[idx + len(old_line):]
    
    if MARKER not in patched:
        marker_line = f"\n{MARKER}\n"
        if "import time" in patched:
            idx = patched.find("import time")
            if idx >= 0:
                end_idx = patched.find("\n", idx)
                if end_idx >= 0:
                    patched = patched[:end_idx + 1] + marker_line + patched[end_idx + 1:]
    
    return patched


def smoke_test():
    """Run smoke test to verify imports work."""
    import tempfile
    import subprocess
    
    sys.path.insert(0, SCRIPT_DIR)
    
    test_code = """
import sys
sys.path.insert(0, '{script_dir}')
import importlib
import signal_analyser

if hasattr(signal_analyser, 'score_supply_chain'):
    result = signal_analyser.score_supply_chain('test_id', 'test_name')
    if isinstance(result, tuple) and len(result) == 2:
        score, band = result
        if isinstance(score, float) and isinstance(band, str):
            print("SMOKE_OK")
        else:
            print("SMOKE_FAIL: score_supply_chain returned wrong types")
            sys.exit(1)
    else:
        print("SMOKE_FAIL: score_supply_chain did not return (float, str)")
        sys.exit(1)
else:
    print("SMOKE_FAIL: score_supply_chain not found")
    sys.exit(1)
""".format(script_dir=SCRIPT_DIR.replace("\\", "\\\\"))
    
    result = subprocess.run(
        [sys.executable, "-c", test_code],
        capture_output=True,
        text=True,
        cwd=SCRIPT_DIR,
        timeout=30
    )
    
    if result.returncode != 0:
        return False, result.stderr
    if "SMOKE_OK" in result.stdout:
        return True, "OK"
    return False, result.stdout


def run(dry_run=False):
    """Main patcher logic."""
    print(f"r1_wire_v3_enrichments_patcher")
    print(f"Signal analyser: {SIGNAL_ANALYSER_PATH}")
    print(f"Dry run: {dry_run}")
    print()
    
    if not os.path.exists(SIGNAL_ANALYSER_PATH):
        print(f"FATAL: signal_analyser.py not found at {SIGNAL_ANALYSER_PATH}")
        return 3
    
    content = read_source(SIGNAL_ANALYSER_PATH)
    
    if already_wired(content):
        print("NOOP: already wired (marker present)")
        return 0
    
    decisions = analyze_imports(content)
    
    print("=== Import Resolution Report ===")
    for enrichment, decision in decisions.items():
        current = decision["current"] or "NONE"
        selected = decision["selected"] or "NONE"
        status = decision["status"]
        
        if status == "CURRENT":
            print(f"  [OK]   {enrichment}: current={current} (no change)")
        elif status == "OUTDATED":
            print(f"  [WIRE] {enrichment}: current={current} -> selected={selected}")
        elif status == "NOT_FOUND":
            print(f"  [SKIP] {enrichment}: import not found in file")
        else:
            print(f"  [???]  {enrichment}: status={status}")
    
    print()
    needs_patch = any(d["status"] == "OUTDATED" for d in decisions.values())
    
    if not needs_patch:
        print("NOOP: all enrichments already at latest versions")
        if not dry_run:
            content_with_marker = content + f"\n{MARKER}\n"
            write_source(SIGNAL_ANALYSER_PATH, content_with_marker)
            print("(marker added)")
        return 0
    
    if dry_run:
        print("DRY RUN: would apply patches, not writing")
        return 0
    
    bak = backup_path(SIGNAL_ANALYSER_PATH)
    print(f"Creating backup: {bak}")
    shutil.copy2(SIGNAL_ANALYSER_PATH, bak)
    
    print("Applying patches...")
    patched = patch_content(content, decisions)
    
    write_source(SIGNAL_ANALYSER_PATH, patched)
    print("Patched file written.")
    
    print("Running smoke test...")
    ok, msg = smoke_test()
    
    if not ok:
        print(f"SMOKE FAILED: {msg}")
        print("Restoring from backup...")
        shutil.copy2(bak, SIGNAL_ANALYSER_PATH)
        print("Backup restored.")
        return 2
    
    print("Smoke test passed.")
    print()
    print("=== PATCH APPLIED SUCCESSFULLY ===")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Wire v3 enrichment modules into signal_analyser.py")
    parser.add_argument("--dry-run", action="store_true", help="Read and plan only, do not write")
    args = parser.parse_args()
    
    try:
        exit_code = run(dry_run=args.dry_run)
        sys.exit(exit_code)
    except Exception as e:
        print(f"FATAL: {e}")
        traceback.print_exc()
        sys.exit(3)


if __name__ == "__main__":
    main()