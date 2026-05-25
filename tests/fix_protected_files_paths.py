#!/usr/bin/env python3
"""
fix_protected_files_paths.py -- Correct PROTECTED_FILES list after Gate 2
surfaced two entries with wrong filenames-or-paths.

Findings from 2026-04-17 run (with correction from Robin):
    - write_service.py              at /home/workspace/zo_mesh/ (was assumed zo_sentinel)
    - inference_router_service.py   at /home/workspace/zo_mesh/ (was wrongly named
                                     inference_router.py; the actual file carries
                                     the '_service' suffix)

Both files are ours, both need protection. The fix:
    1. Change PROTECTED_FILES to a list of (display_name, absolute_path) tuples
    2. Map write_service.py -> /home/workspace/zo_mesh/write_service.py
    3. Replace 'inference_router.py' with 'inference_router_service.py' pointing
       at /home/workspace/zo_mesh/inference_router_service.py
    4. Patch three files in lockstep:
       - gate_2_schema_contracts.py
       - rebaseline_protected_files.py
       - sentinel_directive_generator.py  (align so builder's validator
         and gate's checker agree on the list)
    5. Clean baseline rows for paths that no longer match and for the
       old-named inference_router.py entry
    6. Next gate run baselines everything at correct paths

Idempotent. Re-run is a no-op.
"""
import ast
import duckdb
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DB = "/home/workspace/gate_errors.db"
SENTINEL = Path("/home/workspace/zo_sentinel")
MESH     = Path("/home/workspace/zo_mesh")

RETRIES = 5
BACKOFF = 1.5

# (display_name, absolute_path_as_string)
# display_name is what the ledger stores and what appears in error messages.
# absolute_path is where the file actually lives.
CORRECTED_PROTECTED = [
    # Core pipeline -- sentinel-local
    ("signal_analyser.py",           str(SENTINEL / "signal_analyser.py")),
    ("trust_synthesiser.py",         str(SENTINEL / "trust_synthesiser.py")),
    # Core infrastructure -- in zo_mesh
    ("write_service.py",             str(MESH / "write_service.py")),
    ("inference_router_service.py",  str(MESH / "inference_router_service.py")),
    ("full_schema_bootstrap.py",     str(SENTINEL / "full_schema_bootstrap.py")),
    # Ingest
    ("mcp_scanner.py",               str(SENTINEL / "mcp_scanner.py")),
    ("registry_api.py",              str(SENTINEL / "registry_api.py")),
    # Pending manual patches
    ("attestation_engine.py",        str(SENTINEL / "attestation_engine.py")),
    ("threat_intel_ingestor.py",     str(SENTINEL / "threat_intel_ingestor.py")),
    ("rug_pull_monitor.py",          str(SENTINEL / "rug_pull_monitor.py")),
    # UI
    ("ui_server.py",                 str(SENTINEL / "ui_server.py")),
    ("dashboard.html",               str(SENTINEL / "dashboard.html")),
    ("sentinel_status.html",         str(SENTINEL / "sentinel_status.html")),
    ("approval_workflow.py",         str(SENTINEL / "approval_workflow.py")),
    ("search_api.py",                str(SENTINEL / "search_api.py")),
    ("dashboard_api.py",             str(SENTINEL / "dashboard_api.py")),
    ("forensic_detail_api.py",       str(SENTINEL / "forensic_detail_api.py")),
    ("comparison_api.py",            str(SENTINEL / "comparison_api.py")),
    ("advanced_filter_api.py",       str(SENTINEL / "advanced_filter_api.py")),
    ("manual_override_api.py",       str(SENTINEL / "manual_override_api.py")),
    ("bulk_assess_api.py",           str(SENTINEL / "bulk_assess_api.py")),
]

# Old display names that should be removed from the baseline table because
# they were misspelled or had wrong-location assumptions.
REMOVED_NAMES = {"inference_router.py"}


# =============================================================================
# DB helpers
# =============================================================================

def connect_db():
    for i in range(RETRIES):
        try:
            return duckdb.connect(DB)
        except duckdb.IOException as e:
            if "lock" in str(e).lower() and i < RETRIES - 1:
                time.sleep(BACKOFF * (i + 1))
                continue
            raise
    raise RuntimeError(f"could not acquire {DB} lock")


def clean_stale_baselines(con):
    """Drop rows whose display_name is no longer in CORRECTED_PROTECTED."""
    current_names = {name for name, _ in CORRECTED_PROTECTED}
    all_rows = con.execute(
        "SELECT path FROM protected_file_baseline"
    ).fetchall()
    to_delete = [r[0] for r in all_rows
                 if r[0] in REMOVED_NAMES or r[0] not in current_names]
    for name in to_delete:
        con.execute(
            "DELETE FROM protected_file_baseline WHERE path = ?", [name]
        )
        print(f"  [DEL baseline] {name}")
    return len(to_delete)


# =============================================================================
# File patching
# =============================================================================

def _read(path: Path) -> str:
    return path.read_text()


def _write_atomic(path: Path, new_content: str) -> None:
    if path.suffix == ".py":
        try:
            ast.parse(new_content)
        except SyntaxError as e:
            raise RuntimeError(f"post-patch AST invalid for {path}: {e}")
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    backup = path.with_suffix(path.suffix + f".bak.{ts}")
    backup.write_text(_read(path))
    path.write_text(new_content)
    print(f"  [patched] {path}  (backup: {backup.name})")


def patch_gate_2(path: Path):
    """Replace PROTECTED_FILES list and _check_protected_files lookup."""
    src = _read(path)
    import re

    lines = ["PROTECTED_FILES = ["]
    for name, p in CORRECTED_PROTECTED:
        lines.append(f'    ({name!r}, {p!r}),')
    lines.append("]")
    new_block = "\n".join(lines)

    pat = re.compile(
        r"PROTECTED_FILES\s*=\s*\[[^\]]*\]",
        re.DOTALL,
    )
    if not pat.search(src):
        raise RuntimeError(f"PROTECTED_FILES not found in {path}")
    src = pat.sub(new_block, src, count=1)

    # Loop body: bare-filename form -> tuple form
    old_loop = (
        "        for name in PROTECTED_FILES:\n"
        "            path = SENTINEL / name\n"
    )
    new_loop = (
        "        for name, abs_path_str in PROTECTED_FILES:\n"
        "            path = Path(abs_path_str)\n"
    )
    if old_loop in src:
        src = src.replace(old_loop, new_loop, 1)
    else:
        looser = re.compile(
            r"for name in PROTECTED_FILES:\s*\n"
            r"\s+path = SENTINEL / name",
        )
        if looser.search(src):
            src = looser.sub(
                "for name, abs_path_str in PROTECTED_FILES:\n"
                "            path = Path(abs_path_str)",
                src, count=1,
            )
        elif "for name, abs_path_str in PROTECTED_FILES" in src:
            pass  # already converted
        else:
            raise RuntimeError(
                f"Could not find PROTECTED_FILES loop to patch in {path}"
            )

    _write_atomic(path, src)


def patch_rebaseline_script(path: Path):
    """Replace PROTECTED_FILES list and callsites."""
    src = _read(path)
    import re

    lines = ["PROTECTED_FILES = ["]
    for name, p in CORRECTED_PROTECTED:
        lines.append(f'    ({name!r}, {p!r}),')
    lines.append("]")
    new_block = "\n".join(lines)

    pat = re.compile(
        r"PROTECTED_FILES\s*=\s*\[[^\]]*\]",
        re.DOTALL,
    )
    if not pat.search(src):
        raise RuntimeError(f"PROTECTED_FILES not found in {path}")
    src = pat.sub(new_block, src, count=1)

    # Add lookup dict helper after the list (if not already present)
    helper_insert = (
        "\n# Map display_name -> absolute path for lookups\n"
        "PROTECTED_PATHS = {name: abs_path for name, abs_path in PROTECTED_FILES}\n"
    )
    if "PROTECTED_PATHS = {name:" not in src:
        src = src.replace(new_block, new_block + helper_insert, 1)

    # Path construction inside rebaseline_one
    old_path_construct = "    path = SENTINEL / name"
    new_path_construct = (
        "    abs_path = PROTECTED_PATHS.get(name, str(SENTINEL / name))\n"
        "    path = Path(abs_path)"
    )
    if old_path_construct in src and "PROTECTED_PATHS.get(name" not in src:
        src = src.replace(old_path_construct, new_path_construct, 1)

    # Tuple iteration in cmd_stale / bulk loop
    old_iter = "    for name in PROTECTED_FILES:"
    new_iter = "    for name, _abs in PROTECTED_FILES:"
    if old_iter in src and new_iter not in src:
        src = src.replace(old_iter, new_iter)

    old_bulk = "            for name in PROTECTED_FILES:"
    new_bulk = "            for name, _abs in PROTECTED_FILES:"
    if old_bulk in src:
        src = src.replace(old_bulk, new_bulk)

    # Membership check in CLI arg validation
    old_val = "            if name not in PROTECTED_FILES:"
    new_val = "            if name not in PROTECTED_PATHS:"
    if old_val in src and new_val not in src:
        src = src.replace(old_val, new_val, 1)

    _write_atomic(path, src)


def patch_directive_generator(path: Path):
    """Update the name-set used for directive validation.
    Drop inference_router.py (bad name), add inference_router_service.py."""
    src = _read(path)
    import re

    names_only = {name for name, _ in CORRECTED_PROTECTED}
    lines = ["PROTECTED_FILES = {"]
    for name in sorted(names_only):
        lines.append(f'    {name!r},')
    lines.append("}")
    new_block = "\n".join(lines)

    pat = re.compile(
        r"PROTECTED_FILES\s*=\s*\{[^}]*\}",
        re.DOTALL,
    )
    m = pat.search(src)
    if not m:
        raise RuntimeError(f"PROTECTED_FILES set not found in {path}")

    current = m.group(0)
    # Decide if patch is needed by checking both conditions:
    #  - old bad name present, or
    #  - new correct name absent
    needs_patch = (
        "inference_router.py" in current
        or "inference_router_service.py" not in current
    )
    if not needs_patch:
        print(f"  [skip] {path.name} already aligned")
        return

    src = pat.sub(new_block, src, count=1)
    _write_atomic(path, src)


# =============================================================================
# Main
# =============================================================================

def main():
    print("\n=== Fix protected file paths ===\n")

    # Verify all referenced paths actually exist BEFORE we touch anything
    print("[0/4] Verify all target paths exist on disk")
    missing = []
    for name, p in CORRECTED_PROTECTED:
        if not Path(p).is_file():
            missing.append((name, p))
    if missing:
        print("  [FAIL] These paths are wrong -- refusing to baseline bad data:")
        for name, p in missing:
            print(f"    {name}  ->  {p}")
        print("\nEdit CORRECTED_PROTECTED in this script and re-run.")
        return 2
    print(f"  all {len(CORRECTED_PROTECTED)} paths verified present")

    # 1. Clean stale baselines
    print("\n[1/4] Clean stale baseline rows")
    con = connect_db()
    try:
        deleted = clean_stale_baselines(con)
        print(f"  deleted {deleted} stale row(s)")
    finally:
        con.close()

    # 2. Patch gate_2
    print("\n[2/4] Patch gate_2_schema_contracts.py")
    try:
        patch_gate_2(Path("/home/workspace/zo_sentinel/tests/gates/gate_2_schema_contracts.py"))
    except Exception as e:
        print(f"  [FAIL] {e}")
        return 2

    # 3. Patch rebaseline script
    print("\n[3/4] Patch rebaseline_protected_files.py")
    try:
        patch_rebaseline_script(Path("/home/workspace/zo_sentinel/tests/rebaseline_protected_files.py"))
    except Exception as e:
        print(f"  [FAIL] {e}")
        return 2

    # 4. Patch directive generator
    print("\n[4/4] Patch sentinel_directive_generator.py")
    try:
        patch_directive_generator(Path("/home/workspace/zo_sentinel/sentinel_directive_generator.py"))
    except Exception as e:
        print(f"  [FAIL] {e}")
        return 2

    print("\n=== Done ===")
    print("\nNext: re-run gates. Two files will be newly baselined at their")
    print("correct mesh paths (write_service + inference_router_service).")
    print("\n  python3 /home/workspace/zo_sentinel/tests/gates/run_gates.py \\")
    print("      > /home/workspace/logs/gate_results.txt 2>&1")
    return 0


if __name__ == "__main__":
    sys.exit(main())