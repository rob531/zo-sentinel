#!/usr/bin/env python3
"""
patch_sentinel_directive_generator_quality_map.py

Wire the new quality_map section into the directive generator.

Four edits:
  A. build_prompt() unpacks quality_map from layer1, defaults to placeholder
  B. Prompt template gets a {quality_map} section before 'How to use'
  C. Instructions block gets explicit guidance about breaker state
  D. run_cycle() fallback dict includes 'quality_map' key so the generator
     degrades cleanly if live_quality_map() fails
  E. validate_directive() gains a quarantine check so a quarantined file
     can never be re-proposed even if MiniMax ignores the prompt guidance

Idempotent. AST-validated. Backup on write.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/sentinel_directive_generator.py")

# ── Patch A: build_prompt() signature vars ──────────────────────────────
A_OLD = (
    "    product_spec = layer1.get('product_spec', '[PRODUCT_SPEC unavailable]')\n"
    "    wiring_map   = layer1.get('wiring_map',   '[wiring_map unavailable]')\n"
    "    gaps_map     = layer1.get('gaps_map',     '[gaps_map unavailable]')"
)
A_NEW = (
    "    product_spec = layer1.get('product_spec', '[PRODUCT_SPEC unavailable]')\n"
    "    wiring_map   = layer1.get('wiring_map',   '[wiring_map unavailable]')\n"
    "    gaps_map     = layer1.get('gaps_map',     '[gaps_map unavailable]')\n"
    "    quality_map  = layer1.get('quality_map',  '[quality_map unavailable]')"
)

# ── Patch B: inject {quality_map} in prompt template before 'How to use' ───────
B_OLD = (
    "{gaps_map}\n"
    "\n"
    "## How to use the three sections above"
)
B_NEW = (
    "{gaps_map}\n"
    "\n"
    "{quality_map}\n"
    "\n"
    "## How to use the four sections above"
)

# ── Patch C: add breaker guidance to the 'How to use' bullets ───────────────
C_OLD = (
    "- The gaps map identifies concrete named files / daemons / tables that\n"
    "  the spec asks for but the live system does not yet have. THESE ARE\n"
    "  YOUR PRIMARY DIRECTIVE CANDIDATES."
)
C_NEW = (
    "- The gaps map identifies concrete named files / daemons / tables that\n"
    "  the spec asks for but the live system does not yet have. THESE ARE\n"
    "  YOUR PRIMARY DIRECTIVE CANDIDATES.\n"
    "- The quality map lists Gate 8 breaker state, quarantined files, and\n"
    "  files currently under retry budget. Obey it. If the breaker state is\n"
    "  'tripped', DO NOT propose rebuilds of any listed file. If a file is\n"
    "  quarantined, NEVER propose a rebuild of it. If a file is under retry\n"
    "  budget, you MAY propose a rebuild only if your description references\n"
    "  the listed last_error and the relevant spec section explicitly."
)

# ── Patch D: run_cycle() fallback dict ──────────────────────────────────
D_OLD = (
    "    except Exception as e:\n"
    "        log.warning(\"Layer 1 context assembly failed: %s\", e)\n"
    "        layer1 = {\"product_spec\": \"[Layer 1 unavailable]\",\n"
    "                  \"wiring_map\": \"[Layer 1 unavailable]\",\n"
    "                  \"gaps_map\": \"[Layer 1 unavailable]\"}"
)
D_NEW = (
    "    except Exception as e:\n"
    "        log.warning(\"Layer 1 context assembly failed: %s\", e)\n"
    "        layer1 = {\"product_spec\": \"[Layer 1 unavailable]\",\n"
    "                  \"wiring_map\": \"[Layer 1 unavailable]\",\n"
    "                  \"gaps_map\": \"[Layer 1 unavailable]\",\n"
    "                  \"quality_map\": \"[Layer 1 unavailable]\"}"
)

# ── Patch E: validate_directive() gains quarantine check ─────────────────────
E_OLD = (
    "    if output in PROTECTED_FILES:\n"
    "        return False, f\"protected (hand-calibrated, do not regenerate): {output}\"\n"
    "    if len(d.get(\"description\", \"\")) < 50:\n"
    "        return False, \"description too short (<50 chars)\"\n"
    "    return True, \"ok\""
)
E_NEW = (
    "    if output in PROTECTED_FILES:\n"
    "        return False, f\"protected (hand-calibrated, do not regenerate): {output}\"\n"
    "    # Commit 2: enforce quarantine / retry-budget at validation layer\n"
    "    # so even a non-compliant LLM can't bypass the breaker\n"
    "    try:\n"
    "        import sys as _sys\n"
    "        if '/home/workspace/zo_sentinel' not in _sys.path:\n"
    "            _sys.path.insert(0, '/home/workspace/zo_sentinel')\n"
    "        import gate_quality_state as _gqs\n"
    "        _ok, _reason = _gqs.may_rebuild(output)\n"
    "        if not _ok:\n"
    "            return False, f\"quality gate blocks rebuild of {output}: {_reason}\"\n"
    "    except Exception:\n"
    "        pass  # fail-open on breaker infra error; prompt guidance still applies\n"
    "    if len(d.get(\"description\", \"\")) < 50:\n"
    "        return False, \"description too short (<50 chars)\"\n"
    "    return True, \"ok\""
)


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("sentinel_directive_generator: wire quality_map + quarantine guard")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()
    changed = False

    patches = [
        ("A", "build_prompt unpacks quality_map",  A_OLD, A_NEW, "quality_map  = layer1.get"),
        ("B", "prompt template injects quality_map", B_OLD, B_NEW, "How to use the four sections"),
        ("C", "how-to-use adds breaker guidance",   C_OLD, C_NEW, "quality map lists Gate 8 breaker"),
        ("D", "run_cycle fallback has quality_map", D_OLD, D_NEW, '"quality_map": "[Layer 1 unavailable]"'),
        ("E", "validate_directive quarantine guard", E_OLD, E_NEW, "quality gate blocks rebuild"),
    ]

    for label, desc, old, new, marker in patches:
        if marker in src:
            print(f"  [skip {label}] {desc}: already present")
            continue
        if old not in src:
            print(f"  [FAIL {label}] {desc}: anchor not found verbatim")
            return 2
        src = src.replace(old, new, 1)
        print(f"  [patch {label}] {desc}: applied")
        changed = True

    if not changed:
        print("\n  [noop] all patches already applied")
        return 0

    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid after patch: {e}")
        return 2

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"\n  [done] {TARGET.name} patched")
    print("\nVerify AST:")
    print("  python3 -c \"import ast; ast.parse(open('/home/workspace/zo_sentinel/sentinel_directive_generator.py').read()); print('AST OK')\"")
    print("\nRestart directive generator (it's long-running, picks up new code only via restart):")
    print("  pkill -f 'sentinel_directive_generator.py'")
    print("  sleep 2")
    print("  nohup python3 /home/workspace/zo_sentinel/sentinel_directive_generator.py \\")
    print("    >> /home/workspace/logs/sentinel_sentinel_directive_generator.log 2>&1 &")
    print("")
    print("Then trigger a fresh generation (if queue is high, it'll skip; check log):")
    print("  tail -f /home/workspace/logs/sentinel_sentinel_directive_generator.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())