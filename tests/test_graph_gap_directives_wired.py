"""FU-311 / improvement-loop cycle-0034 (2026-08-10) -- the CONSUMER end of the
capmap knowledge-layer chain is executed by CI, and executed against the graph CI
just regenerated rather than against the stale committed one.

WHY THIS FILE EXISTS
--------------------
FU-304 established that 7 of 14 dark tools were one unwired pipeline:

    scan_capmap -> capmap.json -> build_app_graph -> schema/app_graph.sql
                               -> graph_gap_directives -> promote_graph_directives

PR #3106 wired the two PRODUCERS into the already-live `capmap-check` job. The
CONSUMER stayed dark, which is the link that actually matters: a generated
artifact with no reader is how schema/app_graph.sql drifted for two months in
silence while every surface stayed green.

THE SUBSTANTIVE CLAIM, AND WHY IT IS NOT COSMETIC
-------------------------------------------------
The consumer's own default is `--graph schema/app_graph.sql`, i.e. the COMMITTED
artifact -- which is exactly the stale one. Measured on origin/main 3ba36e2f:

    committed   schema/app_graph.sql    (32,860 B) -> 6 "grounded" gaps:
                                                      3 orphaned-UI + 3 SCHEMA-DRIFT
    regenerated artifacts/app_graph.sql (102,494 B) -> 4 gaps:
                                                      4 orphaned-UI + 0 schema-drift

Those 3 schema_drift directives (mesh_events x2, policy_rules) describe drift that
pull_check -- a BLOCKING gate in the very same job -- reports as ZERO. The stale
graph also MISSES one real orphaned UI (dashboard.html). So a consumer run on its
default input would have handed the builder three fixes for a bug that no longer
exists and hidden one gap that does. That is the house failure class verbatim: the
artifact you inspected is not the artifact that runs.

Hence two positive assertions -- the step EXISTS, and it passes
`--graph artifacts/app_graph.sql` -- plus `--out` confined to artifacts/, which is
the tool's own PROPOSAL-ONLY contract ("never auto-feeds pending/").

NEGATIVE CONTROLS (R4 -- an assertion never seen RED is not evidence)
---------------------------------------------------------------------
Four permanent controls run every time this file does. Each feeds a synthetic job
to the SAME predicate functions the live assertions use and requires False:

  * step absent entirely
  * the invocation present only as a shell COMMENT line -- the FU-305
    anti-regression. dark_tools counts any `tools/<stem>.py` path string as
    invocation-shaped, and #3106's comment flipped two tools that way. A caller
    must be proved from parsed structure with comments stripped, never raw text.
  * the step present but relying on the DEFAULT (stale) graph
  * the step present but writing into the repo's graph_directives/ tree

Observed RED once against origin/main before the fix landed: with pr-gates.yml at
3ba36e2f, `test_capmap_check_executes_the_consumer` FAILS (no step invokes it).

PyYAML is imported hard, not via importorskip: a skip is not a pass, and the
evaluator install step pins PyYAML explicitly so an ImportError here means that
step regressed -- which is a thing we want RED, not silently green.

NOTE ON THE MEASUREMENT LAYER: dark_tools `--assert-wired` resolves against
REF="origin/main", so its rc cannot flip on a branch. The predicate is expected to
stay rc=1 until this merges; these tests are the branch-visible evidence.
"""
import os
import re

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(REPO, ".github", "workflows", "pr-gates.yml")

CONSUMER = "tools/graph_gap_directives.py"
REGENERATED_GRAPH = "artifacts/app_graph.sql"

# `python`, `python3`, `python3.11`, optionally with interpreter flags, then the
# script path. Anchored on an actual interpreter invocation so a bare path string
# in prose can never satisfy it.
_INVOCATION = re.compile(
    r"(?:^|[;&|]\s*)python[0-9.]*\s+(?:-\S+\s+)*" + re.escape(CONSUMER)
)


def _load_job(name="capmap-check"):
    with open(WORKFLOW, encoding="utf-8") as fh:
        wf = yaml.safe_load(fh)
    assert name in wf["jobs"], f"{name} job vanished from pr-gates.yml"
    return wf["jobs"][name]


def _command_lines(job):
    """Every executable line of every `run:` in `job`, comments stripped.

    Whole-line shell comments are dropped BEFORE matching. That is the whole
    point: raw text lies, parsed structure with comments removed does not.
    Backslash continuations are folded so a multi-line invocation reads as one
    command.
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


def executes_consumer(job):
    return bool(_invocations(job))


def _flag(line, flag):
    m = re.search(re.escape(flag) + r"\s+(\S+)", line)
    return m.group(1) if m else None


def reads_regenerated_graph(job):
    return any(_flag(ln, "--graph") == REGENERATED_GRAPH for ln in _invocations(job))


def writes_only_under_artifacts(job):
    inv = _invocations(job)
    if not inv:
        return False
    for ln in inv:
        out = _flag(ln, "--out")
        # No --out at all means the tool's default (<repo>/graph_directives) --
        # inside the repo tree, which is what PROPOSAL-ONLY forbids CI touching.
        if out is None or not out.startswith("artifacts/"):
            return False
    return True


def _synthetic(run):
    return {"steps": [{"name": "synthetic", "run": run}]}


# --------------------------------------------------------------------------
# POSITIVE -- asserted against the LIVE workflow file
# --------------------------------------------------------------------------


def test_capmap_check_executes_the_consumer():
    job = _load_job()
    assert executes_consumer(job), (
        "No step in capmap-check executes %s. The capmap chain's producers run on "
        "every PR and write a graph nothing reads." % CONSUMER
    )


def test_consumer_reads_the_regenerated_graph_not_the_stale_committed_default():
    job = _load_job()
    assert reads_regenerated_graph(job), (
        "%s must be passed --graph %s. Its default is schema/app_graph.sql, the "
        "COMMITTED copy, which on 3ba36e2f emits 3 schema_drift directives for "
        "drift that the blocking pull_check gate in this same job scores as 0."
        % (CONSUMER, REGENERATED_GRAPH)
    )


def test_consumer_writes_only_under_artifacts():
    job = _load_job()
    assert writes_only_under_artifacts(job), (
        "%s is PROPOSAL-ONLY by its own contract. CI may only write its output "
        "under artifacts/, never into graph_directives/ or directives/." % CONSUMER
    )


# --------------------------------------------------------------------------
# NEGATIVE CONTROLS -- these must go RED, or the assertions above measure nothing
# --------------------------------------------------------------------------


def test_negative_control_absent_step_is_red():
    job = _synthetic("python tools/scan_capmap.py artifacts/capmap.json\n")
    assert not executes_consumer(job)
    assert not reads_regenerated_graph(job)
    assert not writes_only_under_artifacts(job)


def test_negative_control_comment_only_mention_is_red():
    """FU-305 anti-regression: a path string in a comment is not a caller."""
    job = _synthetic(
        "# python tools/graph_gap_directives.py --graph artifacts/app_graph.sql\n"
        "  #   python tools/graph_gap_directives.py --out artifacts/graph_directives\n"
        "echo 'this job calls nothing'\n"
    )
    assert not executes_consumer(job), (
        "A commented-out invocation was counted as a caller -- the exact defect "
        "that flipped two tools green in #3106."
    )
    assert not reads_regenerated_graph(job)


def test_negative_control_default_stale_graph_is_red():
    job = _synthetic(
        "python tools/graph_gap_directives.py --out artifacts/graph_directives\n"
    )
    assert executes_consumer(job), "control setup broken: it should still be a caller"
    assert not reads_regenerated_graph(job), (
        "Relying on the tool's default --graph (schema/app_graph.sql) must be RED."
    )


def test_negative_control_writing_into_repo_tree_is_red():
    job = _synthetic(
        "python tools/graph_gap_directives.py --graph artifacts/app_graph.sql "
        "--out graph_directives\n"
    )
    assert executes_consumer(job), "control setup broken: it should still be a caller"
    assert reads_regenerated_graph(job), "control setup broken: --graph is correct here"
    assert not writes_only_under_artifacts(job), (
        "Writing proposals into the repo's graph_directives/ must be RED -- "
        "PROPOSAL-ONLY means a human moves them, not CI."
    )
