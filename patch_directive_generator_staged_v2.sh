#!/usr/bin/env bash
# patch_directive_generator_staged_v2.sh
# ------------------------------------------------------------------------------
# Fix for the v1 patcher -- single-character escaping bug in diversity_fn.
#
# The v1 patcher had `return "\n".join(lines)` inside a bash heredoc using
# triple-single-quoted Python. The heredoc delivered `"\n"` unchanged, but
# Python's triple-single-quote parser then consumed the `\n` as a real newline,
# leaving broken syntax. Fix: write `"\\n"` so heredoc delivers `"\n"` to
# Python which sees the proper escape.
#
# All other logic identical to v1. Idempotent. AST-validated.
# ------------------------------------------------------------------------------
set -uo pipefail

SENTINEL=/home/workspace/zo_sentinel
LOGS=/home/workspace/logs
FILE="$SENTINEL/sentinel_directive_generator.py"
TS="$(date +%Y%m%d_%H%M%S)"

RED=$'\033[91m'; GRN=$'\033[92m'; YLW=$'\033[93m'; CYA=$'\033[96m'; NC=$'\033[0m'
h1()   { printf "\n%s=== %s ===%s\n" "$CYA" "$*" "$NC"; }
ok()   { printf "  %s[OK]%s %s\n" "$GRN" "$NC" "$*"; }
bad()  { printf "  %s[X]%s %s\n"  "$RED" "$NC" "$*"; }
warn() { printf "  %s[!]%s %s\n" "$YLW" "$NC" "$*"; }

h1 "directive generator: staged enrichment orientation (v2)"

[[ -f "$FILE" ]] || { bad "$FILE missing"; exit 2; }
python3 -c "import ast; ast.parse(open('$FILE').read())" 2>/dev/null \
    && ok "source parses cleanly" \
    || { bad "syntax errors present -- aborting"; exit 2; }

if grep -q 'PROTECTED_FILES' "$FILE" && grep -q 'get_signal_diversity_snapshot' "$FILE"; then
    ok "already patched. Skipping."
    exit 0
fi

cp "$FILE" "$FILE.bak.$TS"
ok "backup -> $FILE.bak.$TS"

python3 <<'PYEOF'
import ast
import re

path = "/home/workspace/zo_sentinel/sentinel_directive_generator.py"
src = open(path).read()

# ------- PROTECTED_FILES constant -------------------------------------------
protected_block = '''
# Files that are WORKING and hand-calibrated. Validator rejects any directive
# targeting these. Removal guidance: only remove an entry when the module is
# superseded OR an explicit rebuild directive has been human-approved.
#
# UI entries: kept because they've been served via Zo preview and user-tested.
# As the UI is redesigned, revisit these entries.
PROTECTED_FILES = {
    # Core pipeline -- hand-patched 2026-04-17 after 4-bug debug chain
    "signal_analyser.py",
    "trust_synthesiser.py",
    # Core infrastructure -- never regenerate
    "write_service.py",
    "inference_router.py",
    "full_schema_bootstrap.py",
    # Ingest -- production critical
    "mcp_scanner.py",
    "registry_api.py",
    # Pending manual patches
    "attestation_engine.py",
    "threat_intel_ingestor.py",
    "rug_pull_monitor.py",
    # UI -- served via Zo preview and tested
    "ui_server.py",
    "dashboard.html",
    "sentinel_status.html",
    "approval_workflow.py",
    "search_api.py",
    "dashboard_api.py",
    "forensic_detail_api.py",
    "comparison_api.py",
    "advanced_filter_api.py",
    "manual_override_api.py",
    "bulk_assess_api.py",
}
'''

anchor = 'ALREADY_BUILT = {'
if 'PROTECTED_FILES' not in src:
    if anchor not in src:
        print("ERROR: ALREADY_BUILT anchor not found")
        exit(3)
    src = src.replace(anchor, protected_block + '\n' + anchor, 1)

# ------- get_signal_diversity_snapshot() ------------------------------------
# NOTE: \\n below becomes \n after heredoc, then Python sees proper escape.
# This was the v1 bug -- fixed here.
diversity_fn = '''
def get_signal_diversity_snapshot() -> str:
    """Live diagnostic of signal discrimination. Included in every prompt."""
    rows = ws_query(
        "SELECT signal_name, COUNT(DISTINCT score) AS distinct_vals, "
        "ROUND(MIN(score), 1) AS lo, ROUND(MAX(score), 1) AS hi "
        "FROM mcp_signal_scores "
        "GROUP BY signal_name ORDER BY distinct_vals DESC, signal_name"
    )
    if not rows:
        return "  (mcp_signal_scores empty -- no diagnostic available)"
    lines = ["  signal                       distinct   range           verdict"]
    for r in rows:
        sig = str(r.get("signal_name", "?"))[:28].ljust(28)
        dv = int(r.get("distinct_vals", 0))
        lo = r.get("lo", 0)
        hi = r.get("hi", 0)
        if dv == 1:
            verdict = "BAD -- flat, no discrimination"
            rng = (str(lo) + " flat").ljust(15)
        elif dv < 4:
            verdict = "WEAK -- low variety"
            rng = (str(lo) + " - " + str(hi)).ljust(15)
        else:
            verdict = "OK"
            rng = (str(lo) + " - " + str(hi)).ljust(15)
        lines.append("  " + sig + " " + str(dv).ljust(10) + " " + rng + " " + verdict)
    return chr(10).join(lines)

'''

if 'def get_signal_diversity_snapshot' not in src:
    if 'def build_prompt(' not in src:
        print("ERROR: build_prompt anchor not found")
        exit(3)
    src = src.replace('def build_prompt(', diversity_fn + '\ndef build_prompt(', 1)

# ------- rewrite build_prompt ------------------------------------------------
old_prompt_fn_match = re.search(
    r'def build_prompt\(schema: str, failed: list, failures_detail: str,\n'
    r'\s+registry_summary: str, queue_depth: int\) -> str:\n'
    r'\s+failed_str = .*?return f""".*?"""\n',
    src, re.DOTALL
)
if old_prompt_fn_match is None:
    print("ERROR: build_prompt body pattern not matched")
    exit(3)

# Uses chr(10) where a newline is needed inside the f-string join, to avoid
# backslash-escape complications inside the heredoc.
new_build_prompt = '''def build_prompt(schema: str, failed: list, failures_detail: str,
                registry_summary: str, queue_depth: int) -> str:
    _nl = chr(10)
    failed_str = _nl.join(f"  - {t}" for t in failed[:10]) or "  None"
    diversity  = get_signal_diversity_snapshot()
    protected  = _nl.join(f"  - {f}" for f in sorted(PROTECTED_FILES))
    return f"""You are the Sentinel Directive Generator for ZO-SENTINEL.

Your job: analyze the current build state and generate a JSON array of
between 3 and {MAX_DIRECTIVES} builder directives for the next cycle.

## Current Build State
Registry: {registry_summary}
Queue depth: {queue_depth}
Failed modules:
{failed_str}

Recent smoke failures:
{failures_detail}

## Signal Quality Diagnostic (live)

{diversity}

A signal is only useful if it discriminates between servers. When every
server gets the same score, that signal contributes nothing to the verdict.
The heuristic is: BAD SIGNAL = SAME SIGNAL across all inputs.

## ZO-SENTINEL Knowledge Base
{schema}

## Instructions

Generate a JSON array. Order by priority. Focus areas:

  1. Failed modules that need a fresh attempt
  2. Missing modules from the 'What Is MISSING' section
  3. Quality passes for hollow stubs
  4. Integration glue between existing modules
  5. SIGNAL ENRICHMENT MODULES (highest-priority work right now)

## Signal Enrichment -- Strict Contract

Enrichment modules are NEW files named like '<signal_name>_enrichment.py'.
They are evaluated by an external harness (enrichment_harness.py) against
synthetic inputs, and gated by an evidence query before integration.

Each enrichment MUST:
  - Expose EXACTLY this function signature:
        def compute_score(metadata: dict) -> tuple[float, dict]
    which returns (score in [0.0, 100.0], evidence_dict).
  - Be a pure function. Same input always produces same output.
  - Read MULTIPLE metadata fields (registry_source, age_days, download_count,
    dependency_count, publisher_verified, stars). Reading only one field
    produces weak discrimination and will be rejected.
  - NOT write to the database. NOT import other project modules.
    NOT make network calls. NOT read files at runtime.
  - Complete each compute_score() call in under 2 seconds.
  - Return an evidence dict listing which fields it used and the partial
    scores derived from each (for auditability).

Example shape (supply_chain_enrichment.py):
    def compute_score(metadata: dict) -> tuple[float, dict]:
        score = 50.0
        evidence = {{}}
        if metadata.get("publisher_verified"):
            score += 20.0
            evidence["publisher_verified"] = 20
        age = metadata.get("age_days", 0)
        age_bonus = min(age / 30, 15)
        score += age_bonus
        evidence["age_bonus"] = age_bonus
        deps = metadata.get("dependency_count", 0)
        dep_penalty = min(deps * 0.5, 20)
        score -= dep_penalty
        evidence["dep_penalty"] = -dep_penalty
        score = max(0.0, min(100.0, score))
        return score, evidence

## Idempotency Protection (CRITICAL)

The following files are WORKING and protected. Any directive that targets
them will be REJECTED by the validator. To improve behaviour of these,
propose a NEW enrichment or companion module -- never a rewrite:

{protected}

Return ONLY a valid JSON array. No markdown fences, no preamble.

Example:
[
  {{"task": "build_supply_chain_enrichment", "handler": "generate_file",
    "output_file": "supply_chain_enrichment.py", "complexity": "medium",
    "phase": "12", "priority": 0.95,
    "description": "Pure enrichment module exposing compute_score(metadata) that returns (float, dict). Uses registry_source, age_days, download_count, dependency_count, publisher_verified, stars. No DB writes, no network, no imports of protected modules. Will be exercised by enrichment_harness.py and gated by enrichment_evidence.sql before integration."}}
]
"""

'''

src = src.replace(old_prompt_fn_match.group(0), new_build_prompt, 1)

# ------- validator extension ------------------------------------------------
old_check = 'if output in ALREADY_BUILT:\n        return False, f"already built: {output}"'
new_check = (
    'if output in ALREADY_BUILT:\n'
    '        return False, f"already built: {output}"\n'
    '    if output in PROTECTED_FILES:\n'
    '        return False, f"protected (hand-calibrated, do not regenerate): {output}"'
)
if 'protected (hand-calibrated' not in src:
    if old_check not in src:
        print("ERROR: validator ALREADY_BUILT check not found")
        exit(3)
    src = src.replace(old_check, new_check, 1)

# ------- AST check ----------------------------------------------------------
try:
    ast.parse(src)
except SyntaxError as e:
    print("ERROR: patched source has syntax error at line " + str(e.lineno) + ": " + str(e.msg))
    exit(4)

open(path, 'w').write(src)
print("ok: wrote " + str(len(src)) + " bytes")
PYEOF

RC=$?
if [[ $RC -ne 0 ]]; then
    bad "rewriter failed -- rolling back"
    cp "$FILE.bak.$TS" "$FILE"
    exit $RC
fi

python3 -c "import ast; ast.parse(open('$FILE').read())" 2>/dev/null \
    && ok "post-patch: parses cleanly" \
    || {
        bad "post-patch: syntax error -- rolling back"
        cp "$FILE.bak.$TS" "$FILE"
        exit 4
    }

h1 "Restart directive generator"
if pgrep -f 'python3 .*sentinel_directive_generator.py' >/dev/null 2>&1; then
    pkill -9 -f 'python3 .*sentinel_directive_generator.py' 2>/dev/null
    warn "killed old directive generator"
fi
sleep 2
setsid python3 "$FILE" >> "$LOGS/sentinel_directive_generator.log" 2>&1 <&- &
sleep 4
pid="$(pgrep -f 'python3 .*sentinel_directive_generator.py' 2>/dev/null | head -1)"
if [[ -n "$pid" ]]; then
    ok "directive_generator PID $pid"
else
    bad "failed to start -- last 5 lines:"
    tail -5 "$LOGS/sentinel_directive_generator.log" | sed 's/^/    /'
    exit 4
fi

h1 "Wait 45s for first cycle"
sleep 45

echo
echo "Recent log tail:"
tail -25 "$LOGS/sentinel_directive_generator.log" | sed 's/^/    /'

echo
ok "Done."
echo
echo "Rollback: cp $FILE.bak.$TS $FILE && pkill -9 -f sentinel_directive_generator.py && \\"
echo "          setsid python3 $FILE >> $LOGS/sentinel_directive_generator.log 2>&1 <&- &"