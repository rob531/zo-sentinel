#!/usr/bin/env python3
"""anchor_self_refill.py -- FU-009: the PR-GATED anchor refill DRAFTER.

AUTHORITY MODEL (CofC ruling 2026-07-19, cofc_2026-07-19_FU-009_self_refill.md)
------------------------------------------------------------------------------
Anchor burn is ~8 targets/day and every PHASE refill so far has been a daily
human act (PHASE 4..8b landed on five consecutive mornings). The council
considered three authority models and adopted the PR-GATED lane:

  1. DRAFT, don't decide -- this tool may DRAFT refill sections from gap/burn
     data. Drafting is the ENTIRE grant of authority.
  2. The only exit is a PR -- a draft reaches PRODUCT_SPEC.md solely as a pull
     request that a HUMAN merges. This tool never merges.
  3. No live writes, in code -- this tool never writes PRODUCT_SPEC.md,
     PRODUCT_SPEC_AUTO_ANCHOR.md, or any runtime-read file. `--out` refuses
     those paths in code (see guard_out_path), not by convention.
  4. Never schedules itself -- single-shot CLI; creates no scheduled task; not
     invocable by the runtime architect. Arming ANY schedule for this tool is a
     separate chairman/CofC decision.
  5. Banner -- every draft is headed "MACHINE-DRAFTED (FU-009) -- human review
     required" (em-dash in the emitted text), in the file and in the paste-ready
     section, so a machine draft can never be mistaken for human intent.
  6. <= 8 targets per draft -- one day's burn; a bad draft's blast radius is
     one reviewable day.
  7. Lane-shaped only -- every drafted target carries the three builder-lane
     preconditions (builder_lane.md): real schema in context, a named working
     exemplar, a __main__ self-test gate printing PASS/FAIL. A target that
     cannot carry all three is excluded VISIBLY with a reason, never silently
     reshaped.

This is deliberately NOT zo_sentinel/anchor_refill.py: that module (gated OFF)
appends machine-mined candidates to an auto-anchor file the runtime reads
directly -- the autonomous authority model the council explicitly declined.
This tool's output is a draft .md a human lifts into PRODUCT_SPEC.md via PR.

PURITY: stdlib only (argparse/datetime/os/re/sys). No network, no subprocess,
no gh/git calls, no imports from the sentinel runtime.

INPUT FORMATS (designed around what the repo actually produces)
---------------------------------------------------------------
--spec  PRODUCT_SPEC.md (READ-ONLY input). Parsed for:
          **PHASE <n>[suffix] lanes (...)**            -> next phase number
          - directive candidate: `<name>.py|.html` ... -> dedup set

--gaps  A saved snapshot of directive_knowledge_sources.live_gaps_map() output
        (the repo's real gap/burn surface -- e.g. `python -c "import
        directive_knowledge_sources as k; print(k.live_gaps_map())" > gaps.md`
        on the runtime host, or the gaps_map block copied out of a generator
        context_json). Recognized headings, exactly as live_gaps_map() emits:

          ## Live Gaps Map (spec candidates vs reality)
          ### Spec-named files that do NOT exist yet (primary directive targets)
            - <name.py>                <- unbuilt candidates: the RUNWAY
          ### Spec-named files that exist (may need INTEGRATION, not rebuild)
            - <name.py>
          ### Daemons declared in KNOWN_DAEMONS but stale or never-seen
            - <service>  (age=..., status=...)
          ### Empty tables awaiting user/admin action (NORMAL ...)
            - <table>                  <- honored: NEVER drafted from
          ### Empty tables indicating pipeline gap (INVESTIGATE)
            - <table>                  <- gap-derived draft targets

        "(none ...)" placeholder lines are ignored. Exhaustion = unbuilt
        candidates / burn-per-day (default 8) < --runway-threshold days.

--queue Optional: a spec_target_queue.md-shaped file (Zocomputer Agents;
        orchestrator-owned -- this tool only READS a copy). Entries:

          ### <name>[ + <name2>] -- FU-<n> . P<k> . queued <date>
          - Problem: ...
          - Target shape (...): ...
          - Exemplar: `<file.py>` (...)

        Queue entries are lifted first (by P priority); each is VALIDATED
        against the three preconditions and excluded with a reason if any is
        missing.

OUTPUT: the draft to stdout; `--out <path>` additionally writes it to a draft
.md (and ONLY there). Run with no arguments or `--self-test` to execute the
inline-fixture self-test (prints PASS/FAIL; house exemplar for the gate:
verdict_breakdown_api.py).
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

TOOL_REF = "tools/anchor_self_refill.py"
BANNER = "MACHINE-DRAFTED (FU-009) — human review required"
MAX_TARGETS_PER_DRAFT = 8          # CofC constraint 6 -- hard cap, one day's burn
DEFAULT_BURN_PER_DAY = 8.0         # observed anchor burn
DEFAULT_RUNWAY_THRESHOLD_DAYS = 1.0

# CofC constraint 3: the live anchor and the runtime-read auto-anchor are
# unwritable by this tool, in code. Basename match, case-insensitive.
FORBIDDEN_OUT_BASENAMES = {"product_spec.md", "product_spec_auto_anchor.md"}

# Gap-derived targets must name a REAL, working exemplar (precondition 2).
# registry_source_freshness_report.py is itself an unbuilt spec candidate on
# 2026-07-19, so the report exemplar is the on-disk dashboard_summary_api.py.
GAP_EXEMPLAR = "dashboard_summary_api.py"


class RefusalError(Exception):
    """Raised when an operation would violate a CofC FU-009 constraint."""


# -------------------------------------------------------------------------
# Parsing: anchor spec
# -------------------------------------------------------------------------

_PHASE_RX = re.compile(r"^\*\*PHASE\s+(\d+)([a-z]?)\s+lanes\b", re.MULTILINE)
_CANDIDATE_RX = re.compile(
    r"^-\s+directive candidate:\s+`([A-Za-z0-9_]+\.(?:py|html))`", re.MULTILINE)


def parse_spec(text: str) -> Dict:
    """Parse the anchor: PHASE numbering + existing candidate names (dedup set)."""
    phases = [(int(m.group(1)), m.group(2)) for m in _PHASE_RX.finditer(text)]
    next_phase = (max(n for n, _ in phases) + 1) if phases else 1
    candidates = {m.group(1).lower() for m in _CANDIDATE_RX.finditer(text)}
    return {"phases": phases, "next_phase": next_phase, "candidates": candidates}


# -------------------------------------------------------------------------
# Parsing: live gaps map snapshot
# -------------------------------------------------------------------------

_GAPS_SECTIONS = (
    ("missing", "### Spec-named files that do NOT exist yet"),
    ("built", "### Spec-named files that exist"),
    ("daemons", "### Daemons declared in KNOWN_DAEMONS"),
    ("tables_awaiting", "### Empty tables awaiting user/admin action"),
    ("tables_investigate", "### Empty tables indicating pipeline gap"),
)


def parse_gaps(text: str) -> Dict[str, List]:
    """Parse a live_gaps_map() snapshot into its sections (see module docstring)."""
    out: Dict[str, List] = {k: [] for k, _ in _GAPS_SECTIONS}
    current: Optional[str] = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("###"):
            current = None
            for key, heading in _GAPS_SECTIONS:
                if stripped.startswith(heading):
                    current = key
                    break
            continue
        if stripped.startswith("##"):
            current = None
            continue
        if current is None or not stripped.startswith("- "):
            continue
        item = stripped[2:].strip()
        if item.startswith("("):        # "(none ...)" placeholder
            continue
        first = item.split()[0]
        if current == "daemons":
            out["daemons"].append((first, item))
        else:
            out[current].append(first)
    return out


def runway_days(unbuilt_count: int, burn_per_day: float = DEFAULT_BURN_PER_DAY) -> float:
    """Days of anchor runway left at the given burn rate."""
    if burn_per_day <= 0:
        return float("inf")
    return unbuilt_count / float(burn_per_day)


def should_draft(unbuilt_count: int, burn_per_day: float,
                 threshold_days: float = DEFAULT_RUNWAY_THRESHOLD_DAYS) -> bool:
    """Draft only when runway is below the threshold (anchor near exhaustion)."""
    return runway_days(unbuilt_count, burn_per_day) < threshold_days


# -------------------------------------------------------------------------
# Parsing + validation: spec-target queue entries
# -------------------------------------------------------------------------

_QUEUE_HEAD_RX = re.compile(
    r"^###\s+(?P<names>[a-z0-9_]+(?:\s*\+\s*[a-z0-9_]+)*)\s+[—–-]+\s+"
    r"(?P<fu>FU-\d+)\s*[·.]\s*P(?P<pri>\d+)",
    re.MULTILINE)
_EXEMPLAR_RX = re.compile(r"Exemplar:\s*`?([a-z0-9_]+\.py)`?")
_ACCEPTANCE_RX = re.compile(r"ACCEPTANCE:\s*(.+)")
# Precondition 1 heuristic (draft validator, not a truth oracle -- the human
# reviewer confirms): evidence the entry names real schema / real data access.
_SCHEMA_EVIDENCE_RX = re.compile(
    r"(app\.db|app\.models|:8772/query|alembic|\bmcp_[a-z_]+\b|\bscore_[a-z_]+\b"
    r"|\bZO_[A-Z_]+\b|real (?:table|schema))")
_SELFTEST_EVIDENCE_RX = re.compile(r"(__main__|self-test|ACCEPTANCE:|\bPASS\b)")


def _one_line(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_queue(text: str) -> List[Dict]:
    """Parse spec_target_queue.md-shaped entries under '## Queued'."""
    entries: List[Dict] = []
    matches = list(_QUEUE_HEAD_RX.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end]
        names = [n.strip() for n in m.group("names").split("+")]
        names = [n if n.endswith((".py", ".html")) else n + ".py" for n in names]
        exemplar = _EXEMPLAR_RX.search(body)
        acceptance = _ACCEPTANCE_RX.search(body)
        problem = re.search(r"-\s+Problem:\s*(.*?)(?=\n-\s|\Z)", body, re.DOTALL)
        shape = re.search(r"-\s+Target shape[^\n]*:\s*(.*?)(?=\n-\s+[A-Z]|\Z)",
                          body, re.DOTALL)
        entries.append({
            "names": names,
            "fu": m.group("fu"),
            "priority": int(m.group("pri")),
            "problem": _one_line(problem.group(1)) if problem else "",
            "shape": _one_line(shape.group(1)) if shape else "",
            "exemplar": exemplar.group(1) if exemplar else None,
            "acceptance": _one_line(acceptance.group(1)) if acceptance else None,
            "schema_ok": bool(_SCHEMA_EVIDENCE_RX.search(body)),
            "selftest_ok": bool(_SELFTEST_EVIDENCE_RX.search(body)),
        })
    return entries


def validate_entry(entry: Dict) -> List[str]:
    """Return the list of MISSING builder-lane preconditions (empty == valid)."""
    missing = []
    if not entry["schema_ok"]:
        missing.append("no real-schema evidence (precondition 1)")
    if not entry["exemplar"]:
        missing.append("no named exemplar (precondition 2)")
    if not entry["selftest_ok"]:
        missing.append("no __main__ self-test evidence (precondition 3)")
    return missing


# -------------------------------------------------------------------------
# Target line builders (EXACT anchor format:
#   - directive candidate: `<name>` -- <desc>. Exemplar: `<x>`. ACCEPTANCE: ...; prints PASS.)
# -------------------------------------------------------------------------

def _ensure_pass_suffix(acceptance: str) -> str:
    acceptance = acceptance.rstrip(" .;")
    if "prints PASS" not in acceptance:
        acceptance += "; prints PASS"
    return acceptance + "."


def queue_candidate_line(name: str, entry: Dict) -> Tuple[str, bool]:
    """Anchor-format line for a lifted queue target. Returns (line, synthesized)."""
    desc = entry["problem"]
    if entry["shape"]:
        desc = (desc + " Target shape: " + entry["shape"]).strip()
    desc = desc.rstrip(" .") + "."
    synthesized = entry["acceptance"] is None
    acceptance = entry["acceptance"] or (
        "__main__ self-test with inline fixtures (TestClient + "
        "dependency_overrides -> SQLite when the target is an API route) "
        "asserting the target shape above")
    line = ("- directive candidate: `{name}` -- {desc} Exemplar: `{ex}`. "
            "ACCEPTANCE: {acc}").format(
        name=name, desc=desc, ex=entry["exemplar"],
        acc=_ensure_pass_suffix(acceptance))
    return line, synthesized


def table_candidate(table: str) -> Tuple[str, str]:
    """Gap-derived target for an empty pipeline table (INVESTIGATE class)."""
    name = "{}_pipeline_gap_report.py".format(table)
    line = (
        "- directive candidate: `{name}` -- read-only pipeline-gap report over the "
        "real `{table}` table via :8772/query: report {{\"table\": \"{table}\", "
        "\"rows\": n, \"status\": EMPTY|POPULATED|UNKNOWN}} plus hours since the "
        "newest row, so an empty core table is a REPORT, not a chairman's "
        "discovery. A count that cannot be observed => status UNKNOWN, never OK "
        "(an unknown is not a zero). Markdown + JSON to stdout. Exemplar: "
        "`{ex}`. ACCEPTANCE: __main__ on a synthetic empty `{table}` asserts "
        "status=EMPTY and on one seeded row asserts POPULATED; prints PASS."
    ).format(name=name, table=table, ex=GAP_EXEMPLAR)
    return name, line


def daemon_candidate(service: str) -> Tuple[str, str]:
    """Gap-derived target for a stale/never-seen declared daemon. REPORT-ONLY."""
    name = "{}_liveness_report.py".format(service)
    line = (
        "- directive candidate: `{name}` -- daemon liveness honesty for "
        "`{service}` (declared in KNOWN_DAEMONS, stale/never-seen in the gaps "
        "map): report {{service, last_seen_age_sec, sla_sec, status in "
        "ALIVE|STALE|NEVER_SEEN}} from the real heartbeat rows via :8772/query. "
        "REPORT-ONLY -- it never restarts, signals, or spawns anything (builder "
        "lane). A daemon with zero heartbeat rows ever => NEVER_SEEN, never a "
        "silent pass. Exemplar: `{ex}`. ACCEPTANCE: __main__ on a synthetic "
        "heartbeat older than the SLA asserts STALE and on a missing row asserts "
        "NEVER_SEEN; prints PASS."
    ).format(name=name, service=service, ex=GAP_EXEMPLAR)
    return name, line


# -------------------------------------------------------------------------
# Draft assembly
# -------------------------------------------------------------------------

def build_draft(spec_state: Dict, gaps: Dict, queue_entries: List[Dict],
                date: str, burn_per_day: float = DEFAULT_BURN_PER_DAY,
                max_targets: int = MAX_TARGETS_PER_DRAFT) -> Tuple[Optional[str], Dict]:
    """Assemble the draft. Returns (draft_text or None, meta).

    meta: {"emitted": [{name, source, line}], "excluded": [{name, reason}],
           "synthesized": [names], "runway": float, "next_phase": int}
    """
    max_targets = min(max_targets, MAX_TARGETS_PER_DRAFT)   # constraint 6: hard cap
    taken: set = set()
    emitted: List[Dict] = []
    excluded: List[Dict] = []
    synthesized: List[str] = []
    known = {c.lower() for c in spec_state["candidates"]}
    on_disk = {b.lower() for b in gaps.get("built", [])}

    def _admit(name: str, line: str, source: str) -> None:
        low = name.lower()
        if low in known:
            excluded.append({"name": name, "reason":
                             "ALREADY_IN_SPEC: already a spec candidate"})
            return
        if low in on_disk:
            excluded.append({"name": name, "reason":
                             "ALREADY_ON_DISK: exists (integration, not rebuild)"})
            return
        if low in taken:
            excluded.append({"name": name, "reason": "DUPLICATE in this draft"})
            return
        if len(emitted) >= max_targets:
            excluded.append({"name": name, "reason":
                             "CAP: draft already carries {} targets "
                             "(one day's burn)".format(max_targets)})
            return
        taken.add(low)
        emitted.append({"name": name, "source": source, "line": line})

    # 1) Queue lifts first (P1 before P2 ...), validated against the lane shape.
    for entry in sorted(queue_entries, key=lambda e: e["priority"]):
        missing = validate_entry(entry)
        if missing:
            for name in entry["names"]:
                excluded.append({"name": name, "reason":
                                 "NOT_LANE_SHAPED: " + "; ".join(missing)})
            continue
        for name in entry["names"]:
            line, synth = queue_candidate_line(name, entry)
            _admit(name, line, "spec-target queue {} (P{})".format(
                entry["fu"], entry["priority"]))
            if synth and name.lower() in taken and \
                    any(e["name"] == name for e in emitted):
                synthesized.append(name)

    # 2) Gap-derived: empty pipeline tables (INVESTIGATE class only -- the
    #    awaiting-user class says "do NOT propose fixes" and is honored).
    for table in gaps.get("tables_investigate", []):
        name, line = table_candidate(table)
        _admit(name, line, "gap-derived (empty pipeline table `{}`)".format(table))

    # 3) Gap-derived: stale/never-seen declared daemons.
    for service, raw in gaps.get("daemons", []):
        name, line = daemon_candidate(service)
        _admit(name, line, "gap-derived (stale daemon `{}`: {})".format(service, raw))

    if not emitted:
        return None, {"emitted": [], "excluded": excluded, "synthesized": [],
                      "runway": runway_days(len(gaps.get("missing", [])), burn_per_day),
                      "next_phase": spec_state["next_phase"]}

    n = spec_state["next_phase"]
    unbuilt = len(gaps.get("missing", []))
    rw = runway_days(unbuilt, burn_per_day)
    n_queue = sum(1 for e in emitted if e["source"].startswith("spec-target"))
    n_gap = len(emitted) - n_queue

    header = (
        "**PHASE {n} lanes (chairman spec extension {date}: MACHINE-DRAFTED "
        "(FU-009) refill -- human review required. Context: drafted by {tool} "
        "from the live gaps map -- {unbuilt} unbuilt spec candidate(s) remain "
        "against a ~{burn:g}/day burn (~{rw:.1f} day(s) runway); {nq} target(s) "
        "lifted from the spec-target queue and {ng} drafted from gap data (empty "
        "pipeline tables / stale daemons). Every target carries the three "
        "builder-lane preconditions (real schema in context, named working "
        "exemplar, __main__ self-test gate) -- see builder_lane.md. This section "
        "reaches the anchor only via a human-merged PR; the drafter never writes "
        "the live spec. All read paths via :8772/query; no fabricated rates -- "
        "unknown is a valid answer.)**"
    ).format(n=n, date=date, tool=TOOL_REF, unbuilt=unbuilt, burn=burn_per_day,
             rw=rw, nq=n_queue, ng=n_gap)

    lines: List[str] = []
    lines.append("<!-- {} -->".format(BANNER))
    lines.append("# {}".format(BANNER))
    lines.append("")
    lines.append("Drafted by `{}` on {}. This file is a DRAFT refill for "
                 "PRODUCT_SPEC.md. It reaches the anchor ONLY via a pull request "
                 "that a human merges (CofC 2026-07-19, FU-009). The drafter "
                 "never merges, never writes the live anchor or any runtime-read "
                 "file, and never schedules itself.".format(TOOL_REF, date))
    lines.append("")
    lines.append("Exhaustion snapshot: {} unbuilt spec candidate(s); burn "
                 "~{:g}/day; runway ~{:.1f} day(s).".format(unbuilt, burn_per_day, rw))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Draft refill section (paste-ready, verbatim anchor format)")
    lines.append("")
    lines.append("<!-- {} -->".format(BANNER))
    lines.append(header)
    lines.append("")
    for e in emitted:
        lines.append(e["line"])
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Review notes (NOT for the anchor -- delete when lifting)")
    lines.append("")
    lines.append("Provenance per target:")
    for e in emitted:
        lines.append("- `{}` -- {}".format(e["name"], e["source"]))
    lines.append("")
    if excluded:
        lines.append("Excluded, visibly (CofC constraint 7 -- never silently "
                     "reshaped):")
        for x in excluded:
            lines.append("- `{}` -- {}".format(x["name"], x["reason"]))
        lines.append("")
    if synthesized:
        lines.append("Synthesized ACCEPTANCE (reviewer MUST sharpen the "
                     "assertions): " +
                     ", ".join("`{}`".format(s) for s in synthesized))
        lines.append("")
    lines.append("Cap: {}/{} targets (constraint 6: one day's burn).".format(
        len(emitted), max_targets))
    lines.append("")
    lines.append("Reviewer duties: verify schema names against schema_truth; "
                 "sharpen any synthesized ACCEPTANCE; split shared descriptions "
                 "on multi-target queue lifts; renumber the PHASE if another "
                 "refill merged first; delete this notes section when lifting "
                 "into PRODUCT_SPEC.md.")
    lines.append("")
    draft = "\n".join(lines)
    return draft, {"emitted": emitted, "excluded": excluded,
                   "synthesized": synthesized, "runway": rw, "next_phase": n}


# -------------------------------------------------------------------------
# Output guard (CofC constraint 3, enforced in code)
# -------------------------------------------------------------------------

def guard_out_path(out_path: str, spec_path: Optional[str] = None) -> None:
    """Refuse any --out that is the live anchor, the auto-anchor, or the input."""
    base = os.path.basename(out_path).strip().lower()
    if base in FORBIDDEN_OUT_BASENAMES:
        raise RefusalError(
            "REFUSED: --out targets '{}' -- this tool never writes the live "
            "anchor or the runtime-read auto-anchor (CofC FU-009, constraint 3)."
            .format(os.path.basename(out_path)))
    if spec_path is not None:
        if os.path.normcase(os.path.abspath(out_path)) == \
                os.path.normcase(os.path.abspath(spec_path)):
            raise RefusalError(
                "REFUSED: --out resolves to the --spec input; the drafter never "
                "writes its input (CofC FU-009, constraint 3).")


# -------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------

def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        prog=TOOL_REF,
        description="PR-gated anchor refill DRAFTER (FU-009). Drafts only; a "
                    "human merges. Run with no args for the self-test.")
    p.add_argument("--spec", required=True,
                   help="PRODUCT_SPEC.md path (READ-ONLY input)")
    p.add_argument("--gaps", required=True,
                   help="live_gaps_map() snapshot file (see module docstring)")
    p.add_argument("--queue", default=None,
                   help="optional spec_target_queue.md-shaped file (read a copy)")
    p.add_argument("--out", default=None,
                   help="write the draft .md here (never the anchor)")
    p.add_argument("--date", default=datetime.date.today().isoformat(),
                   help="chairman spec extension date stamp (YYYY-MM-DD)")
    p.add_argument("--burn", type=float, default=DEFAULT_BURN_PER_DAY,
                   help="anchor burn targets/day (default 8)")
    p.add_argument("--runway-threshold", type=float,
                   default=DEFAULT_RUNWAY_THRESHOLD_DAYS,
                   help="draft only when runway (days) is below this (default 1.0)")
    p.add_argument("--max-targets", type=int, default=MAX_TARGETS_PER_DRAFT,
                   help="targets per draft; hard-capped at 8 (CofC constraint 6)")
    p.add_argument("--force", action="store_true",
                   help="draft even when the anchor is not near exhaustion")
    args = p.parse_args(argv)

    if args.out:
        try:
            guard_out_path(args.out, args.spec)
        except RefusalError as e:
            print(str(e), file=sys.stderr)
            return 2

    try:
        with open(args.spec, "r", encoding="utf-8") as fh:
            spec_text = fh.read()
        with open(args.gaps, "r", encoding="utf-8") as fh:
            gaps_text = fh.read()
        queue_text = ""
        if args.queue:
            with open(args.queue, "r", encoding="utf-8") as fh:
                queue_text = fh.read()
    except OSError as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        return 2

    spec_state = parse_spec(spec_text)
    gaps = parse_gaps(gaps_text)
    queue_entries = parse_queue(queue_text) if queue_text else []

    unbuilt = len(gaps.get("missing", []))
    rw = runway_days(unbuilt, args.burn)
    if not should_draft(unbuilt, args.burn, args.runway_threshold) and not args.force:
        print("NO_DRAFT: anchor not near exhaustion -- {} unbuilt candidate(s), "
              "~{:.1f} day(s) runway at {:g}/day (threshold {:g}). "
              "Use --force to draft anyway.".format(
                  unbuilt, rw, args.burn, args.runway_threshold))
        return 0

    draft, meta = build_draft(spec_state, gaps, queue_entries,
                              date=args.date, burn_per_day=args.burn,
                              max_targets=args.max_targets)
    if draft is None:
        print("NO_DRAFT: nothing lane-shaped to draft ({} exclusion(s)):".format(
            len(meta["excluded"])))
        for x in meta["excluded"]:
            print("  - {} -- {}".format(x["name"], x["reason"]))
        return 0

    print(draft)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(draft)
        print("\n[drafted {} target(s) -> {}; next step: open a PR a human "
              "merges]".format(len(meta["emitted"]), args.out), file=sys.stderr)
    return 0


# -------------------------------------------------------------------------
# Self-test (inline fixtures; prints PASS/FAIL -- exemplar: verdict_breakdown_api.py)
# -------------------------------------------------------------------------

_FIXTURE_SPEC = """# ZO-SENTINEL Product Specification (FIXTURE)

**PHASE 8 lanes (chairman spec extension 2026-07-19: fixture. Context: fixture.)**

- directive candidate: `cadence_runtime_trend_report.py` -- fixture. Exemplar: `dashboard_summary_api.py`. ACCEPTANCE: __main__ fixture; prints PASS.

**PHASE 8b lanes (chairman spec extension 2026-07-19: fixture lift.)**

- directive candidate: `score_run_ledger_writer.py` -- fixture. Exemplar: `verdict_breakdown_api.py`. ACCEPTANCE: __main__ fixture; prints PASS.
"""

_FIXTURE_GAPS_EXHAUSTED = """## Live Gaps Map (spec candidates vs reality)

### Spec-named files that do NOT exist yet (primary directive targets)
  - cadence_runtime_trend_report.py

### Spec-named files that exist (may need INTEGRATION, not rebuild)
  - score_run_ledger_writer.py

### Daemons declared in KNOWN_DAEMONS but stale or never-seen
  - rug_pull_monitor  (age=622h, status=stale)

### Empty tables awaiting user/admin action (NORMAL — do NOT propose fixes)
  - org_api_keys

### Empty tables indicating pipeline gap (INVESTIGATE)
  - score_change_events
  - mcp_definition_history
"""

_FIXTURE_GAPS_HEALTHY = """## Live Gaps Map (spec candidates vs reality)

### Spec-named files that do NOT exist yet (primary directive targets)
""" + "".join("  - unbuilt_fixture_{:02d}.py\n".format(i) for i in range(9))

_FIXTURE_GAPS_MANY_TABLES = _FIXTURE_GAPS_EXHAUSTED + \
    "".join("  - overflow_table_{:02d}\n".format(i) for i in range(10))

_FIXTURE_QUEUE = """# Spec-target queue (FIXTURE)

## Queued

### fixture_ledger_writer + fixture_reconciliation_report — FU-001 · P1 · queued 2026-07-19

- Problem: paid vast runs launch without run-ledger entries; the audit cannot
  reconcile live instances/spend against intended runs.
- Target shape (builder lane: ingestion job + read-only report over the real
  schema): a run-ledger table via named alembic migration, all access through
  app.db / app.models joining mcp_llm_axis_scores; plus a read-only
  reconciliation endpoint flagging orphan instances.
- Exemplar: `verdict_breakdown_api.py` (real app.db/app.models access,
  trust_gate, TestClient + dependency_overrides self-test printing PASS/FAIL).

### fixture_vibes_dashboard — FU-099 · P3 · queued 2026-07-19

- Problem: no exemplar, no named tables, just vibes.
- Target shape: a dashboard of vibes.

### cadence_runtime_trend_report — FU-098 · P2 · queued 2026-07-19

- Problem: already a spec candidate; must dedup, not double-draft. Real table
  in context: mcp_server_registry via :8772/query.
- Target shape: a report.
- Exemplar: `dashboard_summary_api.py` (self-test printing PASS/FAIL).
"""


def _self_test() -> int:
    try:
        # -- spec parsing ---------------------------------------------------
        sp = parse_spec(_FIXTURE_SPEC)
        assert sp["next_phase"] == 9, "next_phase: want 9, got {}".format(
            sp["next_phase"])
        assert sp["candidates"] == {"cadence_runtime_trend_report.py",
                                    "score_run_ledger_writer.py"}, sp["candidates"]

        # -- gaps parsing ---------------------------------------------------
        g = parse_gaps(_FIXTURE_GAPS_EXHAUSTED)
        assert g["missing"] == ["cadence_runtime_trend_report.py"], g["missing"]
        assert g["built"] == ["score_run_ledger_writer.py"], g["built"]
        assert [d[0] for d in g["daemons"]] == ["rug_pull_monitor"], g["daemons"]
        assert g["tables_investigate"] == ["score_change_events",
                                           "mcp_definition_history"], g
        assert g["tables_awaiting"] == ["org_api_keys"], g["tables_awaiting"]

        # -- exhaustion math ------------------------------------------------
        assert runway_days(1, 8.0) == 0.125
        assert should_draft(1, 8.0, 1.0) is True
        assert should_draft(9, 8.0, 1.0) is False, "9 unbuilt = 1.125d runway"
        gh = parse_gaps(_FIXTURE_GAPS_HEALTHY)
        assert len(gh["missing"]) == 9 and not should_draft(
            len(gh["missing"]), 8.0, 1.0)

        # -- queue parsing + lane validation --------------------------------
        q = parse_queue(_FIXTURE_QUEUE)
        assert len(q) == 3, "queue entries: want 3, got {}".format(len(q))
        assert q[0]["names"] == ["fixture_ledger_writer.py",
                                 "fixture_reconciliation_report.py"], q[0]
        assert q[0]["priority"] == 1 and q[0]["exemplar"] == \
            "verdict_breakdown_api.py", q[0]
        assert validate_entry(q[0]) == [], validate_entry(q[0])
        bad = validate_entry(q[1])
        assert len(bad) == 3, "vibes entry must miss all 3 preconditions: " + \
            repr(bad)

        # -- draft assembly -------------------------------------------------
        draft, meta = build_draft(sp, g, q, date="2026-07-19", burn_per_day=8.0)
        assert draft is not None
        assert BANNER in draft, "banner missing"
        assert "**PHASE 9 lanes (chairman spec extension 2026-07-19:" in draft
        emitted_names = [e["name"] for e in meta["emitted"]]
        assert emitted_names[0] == "fixture_ledger_writer.py", emitted_names
        assert "fixture_reconciliation_report.py" in emitted_names
        assert "score_change_events_pipeline_gap_report.py" in emitted_names
        assert "mcp_definition_history_pipeline_gap_report.py" in emitted_names
        assert "rug_pull_monitor_liveness_report.py" in emitted_names
        assert len(emitted_names) <= MAX_TARGETS_PER_DRAFT
        excl = {x["name"]: x["reason"] for x in meta["excluded"]}
        assert "fixture_vibes_dashboard.py" in excl and \
            excl["fixture_vibes_dashboard.py"].startswith("NOT_LANE_SHAPED"), excl
        assert "cadence_runtime_trend_report.py" in excl and \
            "ALREADY_IN_SPEC" in excl["cadence_runtime_trend_report.py"], excl
        assert "org_api_keys_pipeline_gap_report.py" not in emitted_names, \
            "awaiting-user table must never be drafted from"
        # every emitted line carries the three preconditions textually
        for e in meta["emitted"]:
            line = e["line"]
            assert line.startswith("- directive candidate: `"), line
            assert "Exemplar: `" in line, "no exemplar: " + line
            assert "ACCEPTANCE: __main__" in line, "no self-test gate: " + line
            assert "prints PASS" in line, "no PASS gate: " + line

        # -- cap: one day's burn, hard -------------------------------------
        g_many = parse_gaps(_FIXTURE_GAPS_MANY_TABLES)
        assert len(g_many["tables_investigate"]) == 12, g_many
        _, meta_many = build_draft(sp, g_many, q, date="2026-07-19",
                                   burn_per_day=8.0, max_targets=99)
        assert len(meta_many["emitted"]) == MAX_TARGETS_PER_DRAFT, \
            "cap: want 8, got {}".format(len(meta_many["emitted"]))
        assert any("CAP:" in x["reason"] for x in meta_many["excluded"]), \
            "cap exclusions must be visible"

        # -- out-path guard (constraint 3, in code) ------------------------
        for bad_out in ("PRODUCT_SPEC.md", "product_spec.md",
                        os.path.join("x", "PRODUCT_SPEC_AUTO_ANCHOR.md")):
            try:
                guard_out_path(bad_out, None)
                raise AssertionError("guard let through " + bad_out)
            except RefusalError:
                pass
        try:
            guard_out_path("same.md", "same.md")
            raise AssertionError("guard let --out == --spec through")
        except RefusalError:
            pass
        guard_out_path("refill_draft_2026-07-19.md", "PRODUCT_SPEC.md")  # ok

    except AssertionError as e:
        print("FAIL: {}".format(e))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 1 or "--self-test" in sys.argv[1:]:
        sys.exit(_self_test())
    sys.exit(main(sys.argv[1:]))
