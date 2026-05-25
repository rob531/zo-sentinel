#!/usr/bin/env python3
"""
patch_gate_8_relax_contracts.py  -- commit 4.2

Fix Gate 8 false positives identified in the builder output audit.

v2 anchors corrected against live file (not speculative shapes).

  1. admin_submissions.html is an SPA: button+onclick+fetch, no <form>.
     Today's html_contract requires <form> for admin_*.html. Loosen to
     accept either:
       a) classical: <form> AND at least one input-ish element
       b) SPA:       at least one <button> AND at least one input-ish element

  2. signal_enrichment_aggregator.py is a daemon, not an enrichment.
     Currently matches name.endswith('enrichment_aggregator.py') so it
     triggers the compute_score() contract. Narrow to:
       name.endswith('_enrichment.py') AND NOT name.endswith('_aggregator.py')

  3. Update expected= message to reflect loosened admin contract.

Idempotent, AST-validated, backup on write.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/tests/gates/gate_8_new_module.py")

# ---- Patch A: loosen _check_html_contract body ----------------------
A_OLD = (
    '    if admin_required:\n'
    '        if not parser.has_form:\n'
    '            return False, "admin_* requires <form> tag -- none found"\n'
    '        if not parser.has_input:\n'
    '            return False, "admin_* requires at least one <input>/<select>/<button>"'
)
A_NEW = (
    '    if admin_required:\n'
    '        # Commit 4.2: accept SPA pattern (button+input) OR classical form\n'
    '        if not parser.has_input:\n'
    '            return False, "admin_* requires at least one <input>/<select>/<button>/<textarea>"\n'
    '        if not (parser.has_form or parser.has_button):\n'
    '            return False, (\n'
    '                "admin_* requires either a <form> or at least one "\n'
    '                "<button> (for SPA-style pages)"\n'
    '            )'
)

# ---- Patch B: narrow enrichment contract pattern ----------------------
B_OLD = (
    '        # Type-specific contract\n'
    '        if name.endswith("_enrichment.py") or name.endswith("enrichment_aggregator.py"):'
)
B_NEW = (
    '        # Type-specific contract\n'
    '        # Commit 4.2: aggregators (e.g. signal_enrichment_aggregator.py)\n'
    '        # are daemons with cycle()/run(), NOT enrichment modules. Only files\n'
    '        # ending in "_enrichment.py" (AND not "_aggregator.py") get the\n'
    '        # compute_score() contract. Fixes false-positive quarantine risk.\n'
    '        if name.endswith("_enrichment.py") and not name.endswith("_aggregator.py"):'
)

# ---- Patch C: update expected= message ------------------------------
C_OLD = (
    '                expected=("<form> with input/select/button present, body not empty"\n'
    '                          if admin_required else "valid html, body not empty"),'
)
C_NEW = (
    '                expected=("admin: input + (form OR button); body>=20c"\n'
    '                          if admin_required else "valid html, body not empty"),'
)

# ---- Patch D: add has_button to _MinimalHTMLChecker.__init__ ----
D_OLD = (
    '        self.has_form = False\n'
    '        self.has_input = False\n'
    '        self.body_chars = 0'
)
D_NEW = (
    '        self.has_form = False\n'
    '        self.has_input = False\n'
    '        self.has_button = False  # commit 4.2: SPA pattern support\n'
    '        self.body_chars = 0'
)

# ---- Patch E: update handle_starttag to track buttons ---------------
E_OLD = (
    '        if tag == "form": self.has_form = True\n'
    '        if tag in ("input", "select", "button", "textarea"): self.has_input = True'
)
E_NEW = (
    '        if tag == "form": self.has_form = True\n'
    '        if tag == "button": self.has_button = True\n'
    '        if tag in ("input", "select", "button", "textarea"): self.has_input = True'
)


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("gate_8_new_module: relax contracts (commit 4.2)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()

    # Marker check
    if "Commit 4.2: aggregators" in src:
        print("  [skip] commit 4.2 relaxations already applied")
        return 0

    patches = [
        ("A", "html_contract accepts SPA button pattern", A_OLD, A_NEW),
        ("B", "enrichment contract excludes aggregators", B_OLD, B_NEW),
        ("C", "expected= message updated",                C_OLD, C_NEW),
        ("D", "_MinimalHTMLChecker.has_button attr",      D_OLD, D_NEW),
        ("E", "handle_starttag tracks has_button",        E_OLD, E_NEW),
    ]

    changed = False
    for label, desc, old, new in patches:
        if old not in src:
            print(f"  [FAIL {label}] {desc}: anchor not found verbatim")
            return 2
        src = src.replace(old, new, 1)
        print(f"  [patch {label}] {desc}")
        changed = True

    if not changed:
        print("  [noop]")
        return 0

    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid after patch: {e}")
        return 2

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"\n  [done] {TARGET.name} patched")
    print("\nVerify:")
    print('  python3 -c "import ast; ast.parse(open(\'/home/workspace/zo_sentinel/tests/gates/gate_8_new_module.py\').read()); print(\'AST OK\')"')
    print("  python3 /home/workspace/zo_sentinel/tests/gates/run_gates.py 8")
    print("\nExpected on next run:")
    print("  - admin_submissions.html PASSES (SPA button pattern accepted)")
    print("  - signal_enrichment_aggregator.py PASSES (no longer tested against enrichment contract)")
    print("  - Gate 8 false-positive rate drops")
    print("\nAlso clear old retry counters to prevent quarantine from stale data:")
    print("  python3 -c \"import sys; sys.path.insert(0, '/home/workspace/zo_sentinel'); \" \\")
    print("    \"import gate_quality_state as gqs; \" \\")
    print("    \"gqs.clear_retry('signal_enrichment_aggregator.py'); \" \\")
    print("    \"gqs.clear_retry('admin_submissions.html'); \" \\")
    print("    \"print('retry counters cleared')\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())