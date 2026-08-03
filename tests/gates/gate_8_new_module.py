#!/usr/bin/env python3
"""
gate_8_new_module.py -- Smoke + contract checks on recently-built files.

Different from Gates 1/2/5/7: those check the live system. This one checks
the builder's NEW OUTPUT. Discovers files built by t1.zo_sentinel_builder
in the last 24h (via mesh_memory), groups them into cohorts, and runs
type-appropriate contract checks on each.

Cohort discovery:
    A cohort is a contiguous group of builds whose `built_at` timestamps
    are within COHORT_GAP_SEC (600s = 10min) of each other. Matches what
    the builder produces: "Found N directive(s)" burst then idle.

    Cohorts with fewer than MIN_COHORT_SIZE files get their checks recorded
    but don't count toward the circuit-breaker threshold. (That matters for
    commit 2. This gate just records.)

Per-type contracts:
    *_enrichment.py    -> compute_score({}) returns (float in [0,100], dict)
    admin_*.html       -> HTML parses, has a <form> and any input/select/button
    *.html             -> HTML parses, body not empty
    *.md               -> file non-empty and parseable
    default *.py       -> importlib.import_module() succeeds, no side effects

Safety (static source inspection BEFORE import):
    For .py files, we grep the raw source for forbidden patterns BEFORE
    importing:
      - DROP TABLE / DELETE FROM on any mcp_* core table is an automatic FAIL
      - os.system / subprocess.call with user-controllable args triggers WARN
    This guards against a MiniMax hallucination that does the wrong thing
    AT IMPORT TIME (e.g., a module-level DROP). The import is the test --
    we must not let a dangerous import happen just to test it.

Self-smoke:
    Before processing real builds, Gate 8 runs a known-good and known-bad
    pair through its own logic. If the expected verdicts don't come back,
    the gate marks itself degraded and writes NOTHING to gate_checks. That
    way a bug in Gate 8 can't poison the feedback loop in commit 2.

Results:
    Uses the existing gate_checks / gate_errors DuckDB tables via the
    shared `self.check()` helper. No new schema. Each file produces one
    or more checks named like: "gate_8: <file> <contract_name>".
"""
import ast
import importlib.util
import json
import re
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Optional

sys.path.insert(0, "/home/workspace/zo_sentinel/tests/gates")
sys.path.insert(0, "/home/workspace/zo_sentinel")
from gate_framework import Gate, gate_run, ws_query
import gate_quality_state as gqs  # commit 2: breaker + quarantine

# Files we NEVER quarantine even if they somehow fail gate checks.
# Mirrors PROTECTED_FILES in sentinel_directive_generator.py -- these
# are hand-calibrated and a spurious gate fault must not evict them.
GATE8_PROTECTED_FILES = {
    'advanced_filter_api.py', 'approval_workflow.py', 'attestation_engine.py',
    'bulk_assess_api.py', 'comparison_api.py', 'dashboard.html',
    'dashboard_api.py', 'forensic_detail_api.py', 'full_schema_bootstrap.py',
    'inference_router_service.py', 'manual_override_api.py', 'mcp_scanner.py',
    'registry_api.py', 'rug_pull_monitor.py', 'search_api.py',
    'sentinel_status.html', 'signal_analyser.py', 'threat_intel_ingestor.py',
    'trust_synthesiser.py', 'ui_server.py', 'write_service.py',
    'sentinel_external_api.py',  # today's commit; hand-tuned via patchers
}

QUARANTINE_DIR = Path("/home/workspace/zo_sentinel/quarantine")
BUILD_ROOT = Path("/home/workspace/zo_sentinel")
_BUILD_ROOT_POSIX = PurePosixPath("/home/workspace/zo_sentinel")


def _identity_key(build: dict) -> str:
    """The key under which a build's failures are counted.

    MUST identify ONE artifact. `Path(build['file']).name` does not: the
    service era made basenames non-unique -- every service emits the same
    five filenames (service.toml, __init__.py, router.py, logic.py,
    contract.py), so one service's failure incremented a counter that gated
    EVERY service. Measured 2026-08-03: `__init__.py` at 19 attempts,
    `service.toml` at 15, `router.py` at 5, all quarantined, while 300+
    copies of each sat on disk. A module quarantines at 3; those counters
    were sums over unrelated artifacts.

    The same truncation corrupted cohort statistics: `_cohort_bump` and
    `_evaluate_file` add the key to a SET, so a cohort of five services each
    emitting `__init__.py` was recorded as size=1 -- which is how
    `cohort_15_n1: size=1 fail=100%` was produced and how the breaker was
    tripped on a population of one.

    So: key on the path RELATIVE TO THE BUILD ROOT. Flat legacy modules keep
    their existing basename key unchanged (relative path == basename), so no
    historical entry is orphaned; nested service-unit members become
    distinct. Absolute paths outside the root fall back to the full path
    rather than the basename -- a path we cannot relativise is still an
    identity, whereas its basename is not.

    Uses PurePosixPath, not Path: `build['file']` is always written by the
    Linux builder, and `Path('/home/workspace/...').is_absolute()` is FALSE on
    Windows -- so a `Path`-based implementation would key differently on CI
    than on the tower and the tests would grade a behaviour prod never runs.
    """
    raw = str(build.get("file") or "").strip().replace("\\", "/")
    if not raw:
        return ""
    p = PurePosixPath(raw)
    if p.is_absolute():
        try:
            return str(p.relative_to(_BUILD_ROOT_POSIX))
        except ValueError:
            return str(p)
    return str(p)


# ---- Tunables -------------------------------------------------------------

COHORT_GAP_SEC   = 600     # builds within 10min of each other = same cohort
LOOKBACK_HOURS   = 24      # only inspect files built in the last 24h
MIN_COHORT_SIZE  = 4       # smaller cohorts record but don't count for breaker
IMPORT_TIMEOUT_S = 10      # unused for now (importlib is synchronous)
MAX_FILES_PER_RUN = 40     # defensive cap -- we shouldn't see more than this

# Core tables that MUST NOT be mutated by a newly-built module's side effects
# at import time. Import a module that does DROP TABLE and your day is ruined.
# Spec §4 immutability requirements.
PROTECTED_CORE_TABLES = {
    "mcp_server_registry",
    "mcp_signal_scores",
    "mcp_threat_associations",
    "mcp_risk_register",
    "mcp_attestations",
    "mcp_signal_enrichments",
    "service_health",
    "mesh_memory",
    "agent_outputs",
}

# Patterns that cause an immediate FAIL without even trying to import
FORBIDDEN_STATIC_PATTERNS = [
    # SQL write ops on core tables -- spec says append-only
    re.compile(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE),
    re.compile(r"DELETE\s+FROM\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE),
    re.compile(r"TRUNCATE\s+(?:TABLE\s+)?([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE),
]


# ---- Utilities ------------------------------------------------------------

def _recent_builds(lookback_hours: int = LOOKBACK_HOURS) -> list[dict]:
    """Fetch all build_artifact entries from mesh_memory in the last N hours.
    Returns list of {task, file, phase, bytes, interface, built_at} dicts.
    Malformed rows are skipped silently (defensive)."""
    # DuckDB rejects parameterized INTERVAL (parse error on `?` inside
    # INTERVAL ? HOUR). Inline the int -- safe because lookback_hours is
    # a module-level constant, not user input.
    sql = (
        "SELECT content, created_at FROM mesh_memory "
        "WHERE agent_id = 't1.zo_sentinel_builder' "
        "AND memory_type = 'build_artifact' "
        f"AND created_at > now() - INTERVAL {int(lookback_hours)} HOUR "
        "ORDER BY created_at ASC"
    )
    rows = ws_query(sql)
    builds = []
    for r in rows:
        try:
            obj = json.loads(r["content"])
            if "file" in obj and "built_at" in obj:
                builds.append(obj)
        except Exception:
            continue
    return builds


def _group_cohorts(builds: list[dict], gap_sec: int = COHORT_GAP_SEC) -> list[list[dict]]:
    """Group builds into cohorts based on built_at proximity.
    Assumes builds arrive sorted ascending by built_at."""
    if not builds:
        return []
    cohorts = [[builds[0]]]
    for b in builds[1:]:
        try:
            prev_ts = _parse_iso(cohorts[-1][-1]["built_at"])
            cur_ts  = _parse_iso(b["built_at"])
            gap = (cur_ts - prev_ts).total_seconds()
        except Exception:
            gap = gap_sec + 1  # treat as new cohort on parse failure
        if gap <= gap_sec:
            cohorts[-1].append(b)
        else:
            cohorts.append([b])
    return cohorts


def _parse_iso(s: str):
    from datetime import datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _static_safety_scan(source: str) -> Optional[str]:
    """Grep source for forbidden patterns. Returns failure reason or None."""
    for pat in FORBIDDEN_STATIC_PATTERNS:
        for m in pat.finditer(source):
            table = m.group(1) if m.groups() else "?"
            if table.lower() in PROTECTED_CORE_TABLES:
                return f"forbidden {m.group(0)[:40]!r} on protected table {table!r}"
    return None


def _import_isolated(py_path: Path):
    """Attempt to import a .py file WITHOUT executing __main__.
    Returns (module | None, error_str | None).
    Uses importlib.util with a unique module name to avoid cache collisions."""
    try:
        spec = importlib.util.spec_from_file_location(
            f"gate8_probe_{py_path.stem}_{int(time.time()*1000)}",
            py_path,
        )
        if spec is None or spec.loader is None:
            return None, "spec_from_file_location returned None"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


class _MinimalHTMLChecker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tag_counts = {}
        self.has_form = False
        self.has_input = False
        self.has_button = False  # commit 4.2: SPA pattern support
        self.body_chars = 0
        self._in_body = False

    def handle_starttag(self, tag, attrs):
        self.tag_counts[tag] = self.tag_counts.get(tag, 0) + 1
        if tag == "body": self._in_body = True
        if tag == "form": self.has_form = True
        if tag == "button": self.has_button = True
        if tag in ("input", "select", "button", "textarea"): self.has_input = True

    def handle_endtag(self, tag):
        if tag == "body": self._in_body = False

    def handle_data(self, data):
        if self._in_body:
            self.body_chars += len(data.strip())


def _check_html_contract(path: Path, admin_required: bool) -> tuple[bool, str]:
    """Return (ok, detail)."""
    try:
        src = path.read_text(errors="replace")
    except Exception as e:
        return False, f"read failed: {e}"
    parser = _MinimalHTMLChecker()
    try:
        parser.feed(src)
    except Exception as e:
        return False, f"html parse failed: {e}"
    if admin_required:
        # Commit 4.2: accept SPA pattern (button+input) OR classical form
        if not parser.has_input:
            return False, "admin_* requires at least one <input>/<select>/<button>/<textarea>"
        if not (parser.has_form or parser.has_button):
            return False, (
                "admin_* requires either a <form> or at least one "
                "<button> (for SPA-style pages)"
            )
    if parser.body_chars < 20:
        return False, f"body too empty ({parser.body_chars} chars)"
    return True, f"{sum(parser.tag_counts.values())} tags, body={parser.body_chars}c"


def _check_enrichment_contract(mod) -> tuple[bool, str]:
    """A valid *_enrichment.py must expose compute_score(dict) -> (float, dict).
    Per spec §5 signal invariant: all enrichment modules implement the same shape."""
    fn = getattr(mod, "compute_score", None)
    if fn is None:
        return False, "missing compute_score() function"
    try:
        result = fn({})
    except Exception as e:
        return False, f"compute_score({{}}) raised: {type(e).__name__}: {e}"
    if not (isinstance(result, tuple) and len(result) == 2):
        return False, f"compute_score returned {type(result).__name__}, expected 2-tuple"
    score, meta = result
    if not isinstance(score, (int, float)):
        return False, f"score is {type(score).__name__}, expected number"
    if not (0 <= score <= 100):
        return False, f"score={score} outside [0,100]"
    if not isinstance(meta, dict):
        return False, f"meta is {type(meta).__name__}, expected dict"
    return True, f"score={score}, meta keys={list(meta.keys())[:5]}"


# ---- Self-smoke -----------------------------------------------------------
# A known-good sample and a known-bad sample. If Gate 8's own logic produces
# the wrong verdict on these, we refuse to record anything for real files.

_SELF_SMOKE_GOOD = '''
def compute_score(signal: dict):
    return (42.0, {"src": "self_smoke", "ok": True})
'''

_SELF_SMOKE_BAD = '''
def compute_score(signal: dict):
    return "not-a-tuple"
'''


def _run_self_smoke() -> tuple[bool, str]:
    """Return (ok, detail). If not ok, Gate 8 marks itself degraded."""
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        good = tdp / "sample_good_enrichment.py"
        bad  = tdp / "sample_bad_enrichment.py"
        good.write_text(_SELF_SMOKE_GOOD)
        bad.write_text(_SELF_SMOKE_BAD)

        # Good file should pass
        mod_g, err_g = _import_isolated(good)
        if mod_g is None:
            return False, f"self-smoke good file failed to import: {err_g}"
        ok_g, detail_g = _check_enrichment_contract(mod_g)
        if not ok_g:
            return False, f"self-smoke good file falsely FAILED contract: {detail_g}"

        # Bad file should fail
        mod_b, err_b = _import_isolated(bad)
        if mod_b is None:
            return False, f"self-smoke bad file failed to import: {err_b}"
        ok_b, detail_b = _check_enrichment_contract(mod_b)
        if ok_b:
            return False, "self-smoke bad file falsely PASSED contract"

    return True, "known-good passed, known-bad failed as expected"


# ---- The Gate -------------------------------------------------------------

class Gate8NewModule(Gate):
    name = "gate_8_new_module"

    def run(self):
        print(f"\n-- {self.name} --")
        # Commit 2: per-cohort tally of pass/fail for breaker accounting
        self._cohort_totals = {}   # {cohort_id: {size: N, fails: M, files_failed: set}}
        self._files_this_run_ok = set()    # files that passed ALL checks this run
        self._files_this_run_bad = {}      # {filename: first_error_str}

        # 1. Self-smoke. If this fails we record ONE check noting degradation
        #    and skip everything else. Nothing gets evaluated against a
        #    broken evaluator.
        ok, detail = _run_self_smoke()
        self.check(
            "gate_8: self-smoke",
            condition=ok,
            error_class="gate_self_degraded",
            expected="known-good passes contract, known-bad fails contract",
            actual=detail,
            remediation=(
                "Gate 8's own _run_self_smoke() is broken. Review _check_enrichment_contract "
                "and _import_isolated before trusting any gate_8 results in this run."
            ),
        )
        if not ok:
            print("    [skip] Gate 8 self-degraded; skipping file evaluation this run")
            return

        # 2. Fetch recent builds
        try:
            builds = _recent_builds()
        except Exception as e:
            self.check(
                "gate_8: mesh_memory query",
                condition=False,
                error_class="infra_unreachable",
                actual=str(e)[:200],
                remediation="Check write_service :8772; Gate 8 needs mesh_memory access",
            )
            return

        if not builds:
            self.check("gate_8: recent builds present", condition=True)
            print("    [info] no builds in last 24h; nothing to evaluate")
            return

        # Defensive cap
        if len(builds) > MAX_FILES_PER_RUN:
            print(f"    [warn] {len(builds)} builds in window; capping at {MAX_FILES_PER_RUN}")
            builds = builds[-MAX_FILES_PER_RUN:]

        cohorts = _group_cohorts(builds)
        print(f"    [info] {len(builds)} builds across {len(cohorts)} cohort(s)")

        # 3. Evaluate each file -- wrap each call to detect if any
        #    self.check() inside it flipped the failure counter.
        for cohort_idx, cohort in enumerate(cohorts, start=1):
            cohort_label = f"cohort_{cohort_idx}_n{len(cohort)}"
            for build in cohort:
                # Accounting key must IDENTIFY the artifact; the basename
                # does not (see _identity_key). Display still uses the
                # basename, and GATE8_PROTECTED_FILES is still matched on
                # the basename, because that set is written in basenames.
                filename = _identity_key(build)
                failures_before = self.failures
                self._evaluate_file(build, cohort_label)
                file_failed = self.failures > failures_before
                self._cohort_bump(cohort_label, file_failed, filename)
                # Per-file retry accounting
                if file_failed:
                    err = self._files_this_run_bad.get(filename, 'unknown')
                    gqs.record_failure(filename, err, cohort_label)
                else:
                    self._files_this_run_ok.add(filename)
                    # A fresh clean build clears previous retry history.
                    # If the file was quarantined, we do NOT auto-release
                    # (that's a human decision) but we do reset the counter.
                    if gqs.retry_count(filename) > 0:
                        gqs.clear_retry(filename)

        # 4. Quarantine files whose retry count reached the cap.
        self._quarantine_overdue(cohort_label if cohorts else 'no_cohort')

        # 5. Record cohort observations AFTER per-file accounting.
        self._record_cohorts_to_breaker()

        # 6. Summary log line for operator
        final_state = gqs.snapshot()
        print(f"    [breaker] state={final_state.get('state')} "
              f"cohorts_tracked={len(final_state.get('recent_cohorts', []))} "
              f"quarantined={len(final_state.get('quarantined', {}))}")

    # -----------------------------------------------------------------
    def _cohort_bump(self, cohort_label: str, failed: bool, filename: str):
        t = self._cohort_totals.setdefault(cohort_label,
            {'size_files': set(), 'files_failed': set()})
        t['size_files'].add(filename)
        if failed:
            t['files_failed'].add(filename)
            self._files_this_run_bad.setdefault(filename,
                f'failed in {cohort_label}')

    # -----------------------------------------------------------------
    def _evaluate_file(self, build: dict, cohort_label: str):
        file_path = Path(build["file"])
        name = file_path.name
        key = _identity_key(build)
        task = build.get("task", "?")
        prefix = f"gate_8: {key}"
        # Track this file in the cohort even if every check passes.
        # KEY, not basename: this is a SET, so keying it on a name shared by
        # every service collapsed a cohort of N services into size=1.
        self._cohort_totals.setdefault(cohort_label,
            {'size_files': set(), 'files_failed': set()})['size_files'].add(key)
        # Remember failures: any self.check(condition=False) in this
        # method or in _evaluate_python counts the file as failed.
        _failures_before = self.failures

        if not file_path.exists():
            self.check(
                f"{prefix} exists [{cohort_label}]",
                condition=False,
                error_class="built_file_missing",
                expected=f"{file_path} present on disk",
                actual="file not found (deleted since build?)",
                file=str(file_path),
                remediation=f"Check why {task} output no longer exists",
            )
            return

        # Dispatch by extension + name pattern
        suffix = file_path.suffix.lower()
        if suffix == ".py":
            self._evaluate_python(file_path, cohort_label, task)
        elif suffix == ".html":
            admin_required = name.startswith("admin_")
            ok, detail = _check_html_contract(file_path, admin_required=admin_required)
            self.check(
                f"{prefix} html_contract [{cohort_label}]",
                condition=ok,
                error_class="html_contract_failed",
                expected=("admin: input + (form OR button); body>=20c"
                          if admin_required else "valid html, body not empty"),
                actual=detail,
                file=str(file_path),
                remediation=f"Inspect {name}; builder may have produced an empty shell",
            )
        elif suffix == ".md":
            size = file_path.stat().st_size
            self.check(
                f"{prefix} markdown_nonempty [{cohort_label}]",
                condition=(size >= 100),
                error_class="markdown_stub",
                expected="at least 100 bytes of content",
                actual=f"{size} bytes",
                file=str(file_path),
                remediation=f"MiniMax may have produced a stub. Review {name} or requeue.",
            )
        else:
            # Unknown extension -- record one informational pass
            self.check(f"{prefix} unknown_type_noop [{cohort_label}]", condition=True)

    # -----------------------------------------------------------------
    def _evaluate_python(self, path: Path, cohort_label: str, task: str):
        name = path.name
        prefix = f"gate_8: {name}"

        # Read source
        try:
            src = path.read_text(errors="replace")
        except Exception as e:
            self.check(
                f"{prefix} readable [{cohort_label}]",
                condition=False,
                error_class="builder_output_unreadable",
                actual=str(e),
                file=str(path),
            )
            return

        # AST parse (faster than import, catches syntax errors cleanly)
        try:
            ast.parse(src)
        except SyntaxError as e:
            self.check(
                f"{prefix} ast_parse [{cohort_label}]",
                condition=False,
                error_class="syntax_error",
                expected="file parses as valid Python",
                actual=f"SyntaxError line {e.lineno}: {e.msg}",
                file=str(path),
                line_no=e.lineno,
                remediation=f"Rebuild {task}; MiniMax emitted invalid Python",
            )
            return

        # Static safety scan BEFORE import
        forbidden = _static_safety_scan(src)
        if forbidden:
            self.check(
                f"{prefix} static_safety [{cohort_label}]",
                condition=False,
                error_class="forbidden_sql_operation",
                expected="no DROP/DELETE/TRUNCATE on protected tables",
                actual=forbidden,
                file=str(path),
                remediation=(
                    f"Spec §4 says core tables are append-only. Quarantine {name} "
                    "and rebuild the directive with explicit no-destructive-SQL constraints."
                ),
            )
            # Do NOT import a file that fails static safety
            return
        else:
            self.check(f"{prefix} static_safety [{cohort_label}]", condition=True)

        # Import test
        mod, import_err = _import_isolated(path)
        if mod is None:
            self.check(
                f"{prefix} import [{cohort_label}]",
                condition=False,
                error_class="import_failed",
                expected="module imports cleanly",
                actual=import_err,
                file=str(path),
                remediation=f"Rebuild {task}; check imports and top-level code",
            )
            return
        else:
            self.check(f"{prefix} import [{cohort_label}]", condition=True)

        # Type-specific contract
        # Commit 4.2: aggregators (e.g. signal_enrichment_aggregator.py)
        # are daemons with cycle()/run(), NOT enrichment modules. Only files
        # ending in "_enrichment.py" (AND not "_aggregator.py") get the
        # compute_score() contract. Fixes false-positive quarantine risk.
        if name.endswith("_enrichment.py") and not name.endswith("_aggregator.py"):
            ok, detail = _check_enrichment_contract(mod)
            self.check(
                f"{prefix} enrichment_contract [{cohort_label}]",
                condition=ok,
                error_class="enrichment_contract_violation",
                expected="compute_score({}) returns (float in [0,100], dict)",
                actual=detail,
                file=str(path),
                remediation=(
                    "Rebuild with explicit reference to signal invariant in PRODUCT_SPEC §5. "
                    "All enrichment modules must implement the same shape."
                ),
            )
        # Default: no further contract beyond 'imports cleanly'. Good enough for v1.


    # -----------------------------------------------------------------
    def _quarantine_overdue(self, cohort_label: str):
        """Move files that have failed MAX_REBUILDS times to quarantine,
        UNLESS they're in GATE8_PROTECTED_FILES. Record the action in the
        breaker state file. Does NOT delete -- only moves."""
        snap = gqs.snapshot()
        retries = snap.get('file_retries', {})
        for filename, meta in list(retries.items()):
            # PROTECTED is a set of basenames; a key is now a relative path.
            if Path(filename).name in GATE8_PROTECTED_FILES:
                continue
            attempts = meta.get('attempts', 0)
            if attempts < gqs.MAX_REBUILDS:
                continue
            if filename in snap.get('quarantined', {}):
                continue  # already quarantined
            # The key is relative to BUILD_ROOT, so this resolves nested
            # service-unit members as well as flat modules. FU-233 gave the
            # RELEASE sweep a tree-aware probe; this is its mirror on the
            # WRITE side -- a root-only probe here re-manufactured the same
            # false 'missing_on_disk' every run, which is why the released
            # entries came back within a day with HIGHER counters.
            src = BUILD_ROOT / filename
            if not src.exists():
                # file was already deleted/moved externally; record state only
                gqs.record_quarantine(filename,
                    f'missing_on_disk after {attempts} fails', attempts)
                continue
            try:
                # Flatten the key for the quarantine filename: a relative
                # path would otherwise need (and silently fail to create)
                # intermediate directories under quarantine/.
                flat = filename.replace('/', '__')
                QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
                dest = QUARANTINE_DIR / flat
                # If a previous quarantined copy exists, suffix with ts
                if dest.exists():
                    import time as _t
                    dest = QUARANTINE_DIR / f'{flat}.{int(_t.time())}'
                src.rename(dest)
                reason = meta.get('last_error', 'unknown')[:200]
                gqs.record_quarantine(filename,
                    f'{attempts} consecutive fails: {reason}', attempts)
                self.check(
                    f'gate_8: {filename} quarantined [{cohort_label}]',
                    condition=True,
                    error_class='quarantined_after_max_rebuilds',
                )
                print(f'    [quarantine] {filename} -> {dest} ({attempts} fails)')
            except Exception as e:
                print(f'    [quarantine FAIL] {filename}: {e}')

    # -----------------------------------------------------------------
    def _record_cohorts_to_breaker(self):
        """Convert this run's per-cohort tallies into breaker updates."""
        for cohort_id, totals in self._cohort_totals.items():
            size = len(totals['size_files'])
            fails = len(totals['files_failed'])
            fail_rate = (fails / size) if size else 0.0
            try:
                gqs.record_cohort(cohort_id, size, fail_rate)
            except Exception as e:
                print(f'    [breaker-record-FAIL] {cohort_id}: {e}')


def main() -> int:
    with gate_run(trigger="manual", host_state="steady-state") as (db, run_id):
        gate = Gate8NewModule(db, run_id)
        gate.run()
        print(f"\nGate 8: {gate.checks - gate.failures}/{gate.checks} checks passed")
        return 1 if gate.failures else 0


if __name__ == "__main__":
    sys.exit(main())