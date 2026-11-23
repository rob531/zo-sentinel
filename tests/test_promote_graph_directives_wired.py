"""improvement-loop cycle-0237 (2026-11-23), FU-NNN -- the HUMAN-REVIEW GATE at the
end of the capmap knowledge-layer chain has a reader in CI, and the reader is
LIST-ONLY: it may never run the --approve/--all mutation.

WHY THIS FILE EXISTS
--------------------
FU-304/FU-311 wired the capmap chain's PRODUCERS and the graph CONSUMER into the
already-live `capmap-check` job:

    scan_capmap -> build_app_graph -> graph_gap_directives  (producer, writes proposals)
                                   -> graph_directives/      (proposal-only, needs_review)
                                   -> promote_graph_directives  <-- THE HUMAN GATE (this file)
                                   -> directives/proposed/   -> proposed_to_pending_promoter
                                   -> pending/ -> goose_runner

graph_gap_directives writes its proposals into artifacts/graph_directives on every PR,
and NOTHING read them -- the gate that surfaces "N graph-gap directives await human
review" was consulted by nothing, so the chain's last link could silently rot if the
producer's payload format drifted. The dark-tool census scored it 4.9 KB, "consulted by
NOTHING; mentioned only in prose".

THE SUBSTANTIVE CLAIM, AND THE AIR-GAP
--------------------------------------
promote_graph_directives has two modes. Default (no flags) = LIST what awaits review
(read-only). --approve/--all = MUTATE: move a proposal into directives/proposed/, one
step closer to goose_runner. The tool's own contract: "The GATE is here: nothing moves
without an explicit --approve/--all ... graph-driven directives are PROPOSAL-ONLY until a
human promotes them; they never auto-build."

So CI may wire ONLY the LIST mode. --approve in CI would auto-promote graph-driven
directives into the builder, defeating the entire safety rail. Hence the positive
assertions below: the step EXISTS, reads --graph-dir artifacts/graph_directives, and
carries NEITHER --approve NOR --all.

NEGATIVE CONTROLS (R4 -- an assertion never seen RED is not evidence)
---------------------------------------------------------------------
Two layers, because a YAML-text assertion alone is a Potemkin wire (Argon-4 caught this
on review 2026-11-23: if artifacts/graph_directives is empty on a clean CI run, the step
hits the empty early-return on ~100% of runs and never exercises the tool's real paths).

  * YAML CONTROLS -- feed a synthetic capmap-check job to the SAME predicate functions
    the live assertions use and require False:
      - step absent entirely
      - the invocation present only as a shell COMMENT line (the FU-305 anti-regression:
        a path string in a comment is not a caller; parse structure with comments
        stripped, never raw text)
      - the step present WITH --approve  (CI must never promote)
      - the step present WITH --all      (CI must never promote)

  * BEHAVIORAL CONTROLS -- actually EXECUTE the tool against a non-empty queue in a
    tmp_path, so the read path and the mutation path are both exercised even when the CI
    artifacts dir is empty:
      - LIST mode on a seeded dir lists the task, exits 0, and does NOT create
        directives/proposed/ (the read-only invariant -- RED the day list mode mutates)
      - --approve moves exactly the named proposal into proposed/ (gate opens only on the
        human flag), stamps from=graph_gap_approved, and strips needs_review

NOTE ON THE MEASUREMENT LAYER: dark_tools `--assert-wired` resolves against
REF="origin/main", so its rc cannot flip on a branch. The real repo caller that flips it
is the `run:` line this file asserts lives in .github/workflows/pr-gates.yml; these tests
are the branch-visible evidence.
"""
import json
import os
import re
import subprocess
import sys

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(REPO, ".github", "workflows", "pr-gates.yml")
TOOL = "tools/promote_graph_directives.py"
GRAPH_DIR = "artifacts/graph_directives"

# `python`, `python3`, `python3.11`, optional interpreter flags, then the script path.
# Anchored on an actual interpreter invocation so a bare path string in prose can never
# satisfy it (mirrors test_graph_gap_directives_wired.py).
_INVOCATION = re.compile(
    r"(?:^|[;&|]\s*)python[0-9.]*\s+(?:-\S+\s+)*" + re.escape(TOOL)
)


def _load_job(name="capmap-check"):
    with open(WORKFLOW, encoding="utf-8") as fh:
        wf = yaml.safe_load(fh)
    assert name in wf["jobs"], f"{name} job vanished from pr-gates.yml"
    return wf["jobs"][name]


def _command_lines(job):
    """Every executable line of every `run:` in `job`, comments stripped.

    Whole-line shell comments are dropped BEFORE matching -- raw text lies, parsed
    structure with comments removed does not. Backslash continuations are folded so a
    multi-line invocation reads as one command.
    """
    lines = []
    for step in job.get("steps") or []:
        script = step.get("run")
        if not isinstance(script, str):
            continue
        kept = [ln for ln in script.splitlines() if not ln.lstrip().startswith("#")]
        folded = re.sub(r"\\\s*\n\s*", " ", "\n".join(kept))
        lines.extend(folded.splitlines())
    return lines


def _invocations(job):
    return [ln for ln in _command_lines(job) if _INVOCATION.search(ln)]


def executes_gate(job):
    return bool(_invocations(job))


def _flag(line, flag):
    m = re.search(re.escape(flag) + r"\s+(\S+)", line)
    return m.group(1) if m else None


def reads_graph_dir(job):
    return any(_flag(ln, "--graph-dir") == GRAPH_DIR for ln in _invocations(job))


def is_list_only(job):
    """No invocation carries the mutation flags. CI may list, never promote."""
    inv = _invocations(job)
    if not inv:
        return False
    for ln in inv:
        if re.search(r"(^|\s)--approve(\s|$)", ln) or re.search(r"(^|\s)--all(\s|$)", ln):
            return False
    return True


def _synthetic(run):
    return {"steps": [{"name": "synthetic", "run": run}]}


# --------------------------------------------------------------------------
# POSITIVE -- asserted against the LIVE workflow file
# --------------------------------------------------------------------------


def test_capmap_check_executes_the_gate():
    job = _load_job()
    assert executes_gate(job), (
        "No step in capmap-check executes %s. graph_gap_directives writes proposals to "
        "%s on every PR and the gate that surfaces them for review reads nothing."
        % (TOOL, GRAPH_DIR)
    )


def test_gate_reads_the_artifacts_graph_dir():
    job = _load_job()
    assert reads_graph_dir(job), (
        "%s must be passed --graph-dir %s -- the dir graph_gap_directives just wrote. "
        "Its default is the repo's graph_directives/, which does not exist in a clean "
        "CI checkout." % (TOOL, GRAPH_DIR)
    )


def test_gate_is_list_only_never_promotes():
    job = _load_job()
    assert is_list_only(job), (
        "%s in CI must be LIST mode only -- no --approve, no --all. Promoting graph-gap "
        "directives is a HUMAN action; CI auto-promoting them defeats the safety rail "
        "that keeps them proposal-only until a human moves them." % TOOL
    )


# --------------------------------------------------------------------------
# YAML NEGATIVE CONTROLS -- these must go RED, or the assertions above measure nothing
# --------------------------------------------------------------------------


def test_negative_control_absent_step_is_red():
    job = _synthetic("python tools/graph_gap_directives.py --graph artifacts/app_graph.sql\n")
    assert not executes_gate(job)
    assert not reads_graph_dir(job)
    assert not is_list_only(job)


def test_negative_control_comment_only_mention_is_red():
    """FU-305 anti-regression: a path string in a comment is not a caller."""
    job = _synthetic(
        "# python tools/promote_graph_directives.py --graph-dir artifacts/graph_directives\n"
        "echo 'this job calls nothing'\n"
    )
    assert not executes_gate(job), (
        "A commented-out invocation was counted as a caller -- the exact defect that "
        "flipped two tools green in #3106."
    )
    assert not reads_graph_dir(job)


def test_negative_control_approve_flag_is_red():
    job = _synthetic(
        "python tools/promote_graph_directives.py --graph-dir artifacts/graph_directives "
        "--approve some_task\n"
    )
    assert executes_gate(job), "control setup broken: it should still be a caller"
    assert not is_list_only(job), (
        "A CI step carrying --approve must be RED: that auto-promotes graph directives "
        "into the builder, which only a human may do."
    )


def test_negative_control_all_flag_is_red():
    job = _synthetic(
        "python tools/promote_graph_directives.py --graph-dir artifacts/graph_directives --all\n"
    )
    assert executes_gate(job), "control setup broken: it should still be a caller"
    assert not is_list_only(job), (
        "A CI step carrying --all must be RED: it promotes EVERY pending proposal, the "
        "most dangerous mutation of the human gate."
    )


# --------------------------------------------------------------------------
# BEHAVIORAL CONTROLS -- execute the real tool so the wire is not Potemkin
# --------------------------------------------------------------------------

_VALID_DIRECTIVE = {
    "task": "serve_ui_demo_page_html",
    "handler": "generate_file",
    "output_file": "app_routes.py",
    "complexity": "low",
    "description": "The UI page 'demo_page.html' is orphaned -- add it to app_routes.UI_FILES "
                   "mapping a clean URL to it, matching how the shell serves the others.",
    "from": "graph_gap",
    "needs_review": True,
    "gap": "orphaned_ui",
}


def _run_tool(tmp_path, *args):
    gd = tmp_path / "graph_directives"
    gd.mkdir(exist_ok=True)
    (gd / f"{_VALID_DIRECTIVE['task']}.json").write_text(
        json.dumps(_VALID_DIRECTIVE), encoding="utf-8")
    proposed = tmp_path / "proposed"
    cmd = [sys.executable, os.path.join(REPO, TOOL),
           "--graph-dir", str(gd), "--proposed-dir", str(proposed), *args]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    return r, gd, proposed


def test_behavioral_list_mode_is_read_only(tmp_path):
    """LIST mode lists the seeded task, exits 0, and NEVER creates proposed/ (the gate
    is closed). RED the day list mode starts mutating."""
    r, gd, proposed = _run_tool(tmp_path)  # no --approve
    assert r.returncode == 0, r.stderr
    assert "awaiting review" in r.stdout
    assert _VALID_DIRECTIVE["task"] in r.stdout
    # read-only: the proposal stays, and the promote target is never even created
    assert (gd / f"{_VALID_DIRECTIVE['task']}.json").exists()
    assert not proposed.exists(), "LIST mode must not create directives/proposed/"


def test_behavioral_approve_opens_the_gate(tmp_path):
    """--approve (the human flag) moves exactly the named proposal into proposed/,
    stamps from=graph_gap_approved, and strips needs_review."""
    task = _VALID_DIRECTIVE["task"]
    r, gd, proposed = _run_tool(tmp_path, "--approve", task)
    assert r.returncode == 0, r.stderr
    moved = proposed / f"{task}.json"
    assert moved.exists(), "--approve must move the proposal into proposed/"
    assert not (gd / f"{task}.json").exists(), "the source proposal must be removed"
    promoted = json.loads(moved.read_text(encoding="utf-8"))
    assert promoted.get("from") == "graph_gap_approved"
    assert "needs_review" not in promoted
