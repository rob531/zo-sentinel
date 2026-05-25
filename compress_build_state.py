#!/usr/bin/env python3
"""
compress_build_state.py -- One-shot BUILD_STATE.md deduplication and compression.

Problem: update_build_state() APPENDS a new line per successful build without
deduplication. With 160+ builds, BUILD_STATE.md has grown to ~15KB.
arcade_toolbench_ingestor.py alone has 50+ entries. This entire file is injected
into EVERY MiniMax prompt as 'What's Already Built'. At 49KB total prompt size,
MiniMax has almost no room left for output. 'Too short (19 bytes)' = '[generation
failed]' = prompt bloat killing generation entirely.

Fix: deduplicate to one entry per output_file (keep latest), sort by filename,
then rewrite the file. Typical reduction: 15KB -> 3KB.

Also fixes update_build_state() in the builder to overwrite rather than append.

Usage:
  python3 /home/workspace/zo_sentinel/compress_build_state.py
"""
import ast, sys, re
from pathlib import Path
from collections import OrderedDict

BUILD_STATE = Path("/home/workspace/zo_sentinel/BUILD_STATE.md")
BUILDER     = Path("/home/workspace/zo_mesh/zo_sentinel_builder.py")

# ── Step 1: Deduplicate BUILD_STATE.md ────────────────────────────────────
print("=" * 60)
print("ZO-SENTINEL BUILD_STATE.md compression")
print("=" * 60)

if not BUILD_STATE.exists():
    print("[!!] BUILD_STATE.md not found")
    sys.exit(1)

lines = BUILD_STATE.read_text().splitlines()
original_size = len(BUILD_STATE.read_bytes())
original_lines = len(lines)

# Parse entries: lines starting with '- `'
header_lines = []
entries = OrderedDict()  # filename -> latest summary line

for line in lines:
    if line.startswith('- `'):
        # Extract filename from backtick
        m = re.match(r'^- `([^`]+)`', line)
        if m:
            fname = m.group(1)
            entries[fname] = line  # later entries overwrite earlier = keep latest
    else:
        if not entries:  # still in header
            header_lines.append(line)

# Rebuild deduped file
deduped_lines = header_lines + [''] + sorted(entries.values(), key=lambda l: re.match(r'^- `([^`]+)`', l).group(1))
content = '\n'.join(deduped_lines) + '\n'

# Backup
backup = BUILD_STATE.parent / "BUILD_STATE.bak.md"
BUILD_STATE.rename(backup)
print(f"[OK] Backup: {backup}")

BUILD_STATE.write_text(content)
new_size = len(BUILD_STATE.read_bytes())
new_lines = len(content.splitlines())

print(f"[OK] Deduplication complete:")
print(f"     Lines:  {original_lines} -> {new_lines} ({original_lines - new_lines} removed)")
print(f"     Size:   {original_size//1024}KB -> {new_size//1024}KB")
print(f"     Files:  {len(entries)} unique output files")

# ── Step 2: Fix update_build_state() in builder to deduplicate ────────────
print()
print("Patching builder: update_build_state() deduplication...")

if not BUILDER.exists():
    print("[!!] Builder not found, skipping patch")
    sys.exit(0)

original = BUILDER.read_text()

OLD_UPDATE = '''def update_build_state(task: str, output_file: str, interface_summary: str):
    line = "- `" + output_file + "` (" + task + "): " + interface_summary + "\\n"
    if not BUILD_STATE_PATH.exists():
        BUILD_STATE_PATH.write_text("# ZO-SENTINEL Build State\\n\\n## Successfully Built Files\\n")
    with open(BUILD_STATE_PATH, "a") as f:
        f.write(line)
    log.info("  BuildState: +%s", output_file)'''

NEW_UPDATE = '''def update_build_state(task: str, output_file: str, interface_summary: str):
    """Upsert: overwrite existing entry for output_file, or append if new.
    v1.9.3: prevents BUILD_STATE.md growing unboundedly with repeated builds.
    """
    new_line = "- `" + output_file + "` (" + task + "): " + interface_summary
    if not BUILD_STATE_PATH.exists():
        BUILD_STATE_PATH.write_text(
            "# ZO-SENTINEL Build State\\n"
            "# Injected into every generation prompt so files know what exists.\\n\\n"
            "## Successfully Built Files\\n" + new_line + "\\n"
        )
        log.info("  BuildState: +%s", output_file)
        return
    existing = BUILD_STATE_PATH.read_text().splitlines()
    prefix   = "- `" + output_file + "`"
    replaced = False
    new_lines = []
    for l in existing:
        if l.startswith(prefix):
            if not replaced:
                new_lines.append(new_line)  # overwrite first match
                replaced = True
            # skip subsequent duplicates
        else:
            new_lines.append(l)
    if not replaced:
        new_lines.append(new_line)  # new file, append
    BUILD_STATE_PATH.write_text("\\n".join(new_lines) + "\\n")
    log.info("  BuildState: %s%s", "+" if not replaced else "~", output_file)'''

if OLD_UPDATE in original:
    patched = original.replace(OLD_UPDATE, NEW_UPDATE, 1)
    # Also bump version
    patched = patched.replace(
        "ZO-SENTINEL Builder v1.9.3",  # from previous patch
        "ZO-SENTINEL Builder v1.9.3-bs",
        1
    ).replace(
        "ZO-SENTINEL Builder v1.9.2",  # in case previous patch wasn't applied
        "ZO-SENTINEL Builder v1.9.3-bs",
        1
    )
    try:
        ast.parse(patched)
        print("[OK] Syntax check: PASS")
    except SyntaxError as e:
        print(f"[!!] SYNTAX ERROR: {e} - aborting")
        sys.exit(1)
    BUILDER.write_text(patched)
    print("[OK] update_build_state() patched: now uses upsert semantics")
else:
    print("[--] Could not locate OLD update_build_state -- may already be patched, skipping")

print()
print("Summary:")
print(f"  BUILD_STATE.md compressed: {original_size//1024}KB -> {new_size//1024}KB")
print(f"  Builder patched: future builds will upsert, not append")
print()
print("Next: run python3 /home/workspace/zo_sentinel/fix_builder_v193.py then zm go")
print("[DONE]")