#!/usr/bin/env python3
"""
patch_gate_8_add_breaker_and_quarantine.py

Extend Gate 8 with circuit-breaker accounting and quarantine logic
(commit 2).

What this adds:
  A. Import gate_quality_state module and PROTECTED_FILES set.
  B. In _evaluate_file: after recording checks, aggregate pass/fail per
     cohort; at END of run, call gqs.record_cohort() for each observed
     cohort, which updates the breaker.
  C. When a file fails any contract check, call gqs.record_failure()
     exactly once (not per check).
  D. When a file SUCCEEDS all checks, clear any prior retry count.
  E. When a file's retry count hits MAX_REBUILDS and it's NOT in
     PROTECTED_FILES, move it to /home/workspace/zo_sentinel/quarantine/
     and call gqs.record_quarantine(). Protected files are NEVER moved
     no matter how many times they fail.
  F. Runtime summary: at end of run, print breaker state + cohort
     snapshot so operator can see it in log.

What this does NOT do:
  - Does not change self-smoke or discovery logic
  - Does not change existing check recording shapes
  - Does not auto-reset breaker or auto-release quarantine

Idempotent (marker-guarded). AST-validated. Backup on write.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/tests/gates/gate_8_new_module.py")

# ── Patch A: imports + PROTECTED_FILES set ──────────────────────────────────

A_OLD = (
    "sys.path.insert(0, \"/home/workspace/zo_sentinel/tests/gates\")\n"
    "from gate_framework import Gate, gate_run, ws_query"
)
A_NEW = (
    "sys.path.insert(0, \"/home/workspace/zo_sentinel/tests/gates\")\n"
    "sys.path.insert(0, \"/home/workspace/zo_sentinel\")\n"
    "from gate_framework import Gate, gate_run, ws_query\n"
    "import gate_quality_state as gqs  # commit 2: breaker + quarantine\n"
    "\n"
    "# Files we NEVER quarantine even if they somehow fail gate checks.\n"
    "# Mirrors PROTECTED_FILES in sentinel_directive_generator.py -- these\n"
    "# are hand-calibrated and a spurious gate fault must not evict them.\n"
    "GATE8_PROTECTED_FILES = {\n"
    "    'advanced_filter_api.py', 'approval_workflow.py', 'attestation_engine.py',\n"
    "    'bulk_assess_api.py', 'comparison_api.py', 'dashboard.html',\n"
    "    'dashboard_api.py', 'forensic_detail_api.py', 'full_schema_bootstrap.py',\n"
    "    'inference_router_service.py', 'manual_override_api.py', 'mcp_scanner.py',\n"
    "    'registry_api.py', 'rug_pull_monitor.py', 'search_api.py',\n"
    "    'sentinel_status.html', 'signal_analyser.py', 'threat_intel_ingestor.py',\n"
    "    'trust_synthesiser.py', 'ui_server.py', 'write_service.py',\n"
    "    'sentinel_external_api.py',  # today's commit; hand-tuned via patchers\n"
    "}\n"
    "\n"
    "QUARANTINE_DIR = Path(\"/home/workspace/zo_sentinel/quarantine\")"
)

# ── Patch B: add cohort aggregator init to Gate8NewModule.__init__ ────────────────
# We need to track per-cohort pass/fail counts during the run so end-of-run
# can call gqs.record_cohort() once per cohort. Add an attribute by
# overriding run() with a pre-step. Rather than redefine __init__ (which
# the base class controls), we'll add instance attrs lazily in run().

B_OLD = (
    "class Gate8NewModule(Gate):\n"
    "    name = \"gate_8_new_module\"\n"
    "\n"
    "    def run(self):\n"
    "        print(f\"\\n-- {self.name} --\")"
)
B_NEW = (
    "class Gate8NewModule(Gate):\n"
    "    name = \"gate_8_new_module\"\n"
    "\n"
    "    def run(self):\n"
    "        print(f\"\\n-- {self.name} --\")\n"
    "        # Commit 2: per-cohort tally of pass/fail for breaker accounting\n"
    "        self._cohort_totals = {}   # {cohort_id: {size: N, fails: M, files_failed: set}}\n"
    "        self._files_this_run_ok = set()    # files that passed ALL checks this run\n"
    "        self._files_this_run_bad = {}      # {filename: first_error_str}"
)

# ── Patch C: replace _evaluate_file dispatch so it tracks per-cohort status ─────
# The existing _evaluate_file calls self.check() multiple times. We need
# to know which file failed so we can increment retry once per file. Do it
# by wrapping each branch's result in a small helper.

C_OLD = (
    "    # -----------------------------------------------------------------\n"
    "    def _evaluate_file(self, build: dict, cohort_label: str):\n"
    "        file_path = Path(build[\"file\"])\n"
    "        name = file_path.name\n"
    "        task = build.get(\"task\", \"?\")\n"
    "        prefix = f\"gate_8: {name}\""
)
C_NEW = (
    "    # -----------------------------------------------------------------\n"
    "    def _cohort_bump(self, cohort_label: str, failed: bool, filename: str):\n"
    "        t = self._cohort_totals.setdefault(cohort_label,\n"
    "            {'size_files': set(), 'files_failed': set()})\n"
    "        t['size_files'].add(filename)\n"
    "        if failed:\n"
    "            t['files_failed'].add(filename)\n"
    "            self._files_this_run_bad.setdefault(filename,\n"
    "                f'failed in {cohort_label}')\n"
    "\n"
    "    # -----------------------------------------------------------------\n"
    "    def _evaluate_file(self, build: dict, cohort_label: str):\n"
    "        file_path = Path(build[\"file\"])\n"
    "        name = file_path.name\n"
    "        task = build.get(\"task\", \"?\")\n"
    "        prefix = f\"gate_8: {name}\"\n"
    "        # Track this file in the cohort even if every check passes\n"
    "        self._cohort_totals.setdefault(cohort_label,\n"
    "            {'size_files': set(), 'files_failed': set()})['size_files'].add(name)\n"
    "        # Remember failures: any self.check(condition=False) in this\n"
    "        # method or in _evaluate_python counts the file as failed.\n"
    "        _failures_before = self.failures"
)

# ── Patch D: hook end of _evaluate_file to reconcile failure bookkeeping ───────
# The existing _evaluate_file returns implicitly after its dispatch. We
# need a hook that runs after all its self.check() calls to see if the
# failure counter advanced. Easiest: wrap each of its four early-returns.
# But that's surgical to 4 sites. Cleaner: add an after-dispatch at the
# bottom of the method, and also update the 3 early-return branches.
#
# Actually simplest: intercept AT THE CALL SITE in run(), not inside
# _evaluate_file. Move the _failures_before/_after check out there.
# Patch C just adds the counter save; we DON'T modify _evaluate_file
# further. The detection happens in run().
#
# That means we need to ALSO rewrite the run() loop to do the before/after
# check and call _cohort_bump().

D_OLD = (
    "        # 3. Evaluate each file\n"
    "        for cohort_idx, cohort in enumerate(cohorts, start=1):\n"
    "            cohort_label = f\"cohort_{cohort_idx}_n{len(cohort)}\"\n"
    "            for build in cohort:\n"
    "                self._evaluate_file(build, cohort_label)"
)
D_NEW = (
    "        # 3. Evaluate each file -- wrap each call to detect if any\n"
    "        #    self.check() inside it flipped the failure counter.\n"
    "        for cohort_idx, cohort in enumerate(cohorts, start=1):\n"
    "            cohort_label = f\"cohort_{cohort_idx}_n{len(cohort)}\"\n"
    "            for build in cohort:\n"
    "                filename = Path(build['file']).name\n"
    "                failures_before = self.failures\n"
    "                self._evaluate_file(build, cohort_label)\n"
    "                file_failed = self.failures > failures_before\n"
    "                self._cohort_bump(cohort_label, file_failed, filename)\n"
    "                # Per-file retry accounting\n"
    "                if file_failed:\n"
    "                    err = self._files_this_run_bad.get(filename, 'unknown')\n"
    "                    gqs.record_failure(filename, err, cohort_label)\n"
    "                else:\n"
    "                    self._files_this_run_ok.add(filename)\n"
    "                    # A fresh clean build clears previous retry history.\n"
    "                    # If the file was quarantined, we do NOT auto-release\n"
    "                    # (that's a human decision) but we do reset the counter.\n"
    "                    if gqs.retry_count(filename) > 0:\n"
    "                        gqs.clear_retry(filename)\n"
    "\n"
    "        # 4. Quarantine files whose retry count reached the cap.\n"
    "        self._quarantine_overdue(cohort_label if cohorts else 'no_cohort')\n"
    "\n"
    "        # 5. Record cohort observations AFTER per-file accounting.\n"
    "        self._record_cohorts_to_breaker()\n"
    "\n"
    "        # 6. Summary log line for operator\n"
    "        final_state = gqs.snapshot()\n"
    "        print(f\"    [breaker] state={final_state.get('state')} \"\n"
    "              f\"cohorts_tracked={len(final_state.get('recent_cohorts', []))} \"\n"
    "              f\"quarantined={len(final_state.get('quarantined', {}))}\")"
)

# ── Patch E: add the two new helper methods (_quarantine_overdue + _record_cohorts_to_breaker) ──
# Inject these before the `def main() -> int:` that closes out the file.

E_OLD = (
    "def main() -> int:\n"
    "    with gate_run(trigger=\"manual\", host_state=\"steady-state\") as (db, run_id):\n"
    "        gate = Gate8NewModule(db, run_id)"
)
E_NEW = (
    "    # -----------------------------------------------------------------\n"
    "    def _quarantine_overdue(self, cohort_label: str):\n"
    "        \"\"\"Move files that have failed MAX_REBUILDS times to quarantine,\n"
    "        UNLESS they're in GATE8_PROTECTED_FILES. Record the action in the\n"
    "        breaker state file. Does NOT delete -- only moves.\"\"\"\n"
    "        snap = gqs.snapshot()\n"
    "        retries = snap.get('file_retries', {})\n"
    "        for filename, meta in list(retries.items()):\n"
    "            if filename in GATE8_PROTECTED_FILES:\n"
    "                continue\n"
    "            attempts = meta.get('attempts', 0)\n"
    "            if attempts < gqs.MAX_REBUILDS:\n"
    "                continue\n"
    "            if filename in snap.get('quarantined', {}):\n"
    "                continue  # already quarantined\n"
    "            src = Path('/home/workspace/zo_sentinel') / filename\n"
    "            if not src.exists():\n"
    "                # file was already deleted/moved externally; record state only\n"
    "                gqs.record_quarantine(filename,\n"
    "                    f'missing_on_disk after {attempts} fails', attempts)\n"
    "                continue\n"
    "            try:\n"
    "                QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)\n"
    "                dest = QUARANTINE_DIR / filename\n"
    "                # If a previous quarantined copy exists, suffix with ts\n"
    "                if dest.exists():\n"
    "                    import time as _t\n"
    "                    dest = QUARANTINE_DIR / f'{filename}.{int(_t.time())}'\n"
    "                src.rename(dest)\n"
    "                reason = meta.get('last_error', 'unknown')[:200]\n"
    "                gqs.record_quarantine(filename,\n"
    "                    f'{attempts} consecutive fails: {reason}', attempts)\n"
    "                self.check(\n"
    "                    f'gate_8: {filename} quarantined [{cohort_label}]',\n"
    "                    condition=True,\n"
    "                    error_class='quarantined_after_max_rebuilds',\n"
    "                )\n"
    "                print(f'    [quarantine] {filename} -> {dest} ({attempts} fails)')\n"
    "            except Exception as e:\n"
    "                print(f'    [quarantine FAIL] {filename}: {e}')\n"
    "\n"
    "    # -----------------------------------------------------------------\n"
    "    def _record_cohorts_to_breaker(self):\n"
    "        \"\"\"Convert this run's per-cohort tallies into breaker updates.\"\"\"\n"
    "        for cohort_id, totals in self._cohort_totals.items():\n"
    "            size = len(totals['size_files'])\n"
    "            fails = len(totals['files_failed'])\n"
    "            fail_rate = (fails / size) if size else 0.0\n"
    "            try:\n"
    "                gqs.record_cohort(cohort_id, size, fail_rate)\n"
    "            except Exception as e:\n"
    "                print(f'    [breaker-record-FAIL] {cohort_id}: {e}')\n"
    "\n"
    "\n"
    "def main() -> int:\n"
    "    with gate_run(trigger=\"manual\", host_state=\"steady-state\") as (db, run_id):\n"
    "        gate = Gate8NewModule(db, run_id)"
)


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("gate_8_new_module: add circuit breaker + quarantine (commit 2)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()
    changed = False

    patches = [
        ("A", "imports + PROTECTED_FILES",          A_OLD, A_NEW, "import gate_quality_state"),
        ("B", "run() cohort state init",            B_OLD, B_NEW, "self._cohort_totals = {}"),
        ("C", "_evaluate_file cohort tracking",     C_OLD, C_NEW, "def _cohort_bump(self"),
        ("D", "run loop -- retry accounting",       D_OLD, D_NEW, "self._record_cohorts_to_breaker()"),
        ("E", "quarantine + breaker helpers",       E_OLD, E_NEW, "def _quarantine_overdue"),
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
    print("\nVerify:")
    print("  python3 -c 'import ast; ast.parse(open(\"/home/workspace/zo_sentinel/tests/gates/gate_8_new_module.py\").read()); print(\"AST OK\")'")
    print("  python3 /home/workspace/zo_sentinel/tests/gates/run_gates.py 8")
    print("\nExpected on first run post-patch:")
    print("  - Same existing PASS/FAIL pattern as before")
    print("  - Breaker state reported at bottom: state=closed ... quarantined=0")
    print("  - For signal_enrichment_aggregator.py and admin_submissions.html:")
    print("    attempts goes from 0 -> 1 (not yet at cap of 3)")
    print("  - reset_breaker.py status will show retry entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())