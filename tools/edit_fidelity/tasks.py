"""Task selection over the LIVE repo, with the validity gate.

A task is (source_file, test_target, corruption). The gate that makes a task
valid -- and the reason this eval has a ground truth that "grade against green
CI" does not -- is:

    VALID  <=>  test_target PASSES (>= 1 test collected) on the clean file
                AND FAILS on the corrupted file.

Corruptions that leave the target green are DISCARDED AND COUNTED. That count
is reported, never hidden: a mutation the suite cannot see is a statement
about this repo's coverage, and this repo's own scars include a green that ran
zero tests. A gate with a zero-collected clean run is treated as NOT passing.

Mapping test -> source: imports anywhere in the test module (module scope OR
inside functions -- the repo's rule pushes imports into test bodies) plus any
string literal naming an existing ``*.py`` in the repo. Source files must be
inside the repo, not under tests/, and within a size window so the model arm's
token cost stays bounded (the window is a reported parameter, not a secret).

All file writes are guarded: the original bytes are restored in ``finally``
and the restore is verified byte-for-byte before the next candidate.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import os
import pathlib
import random
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import corrupt as C
from . import metrics as M

DEFAULT_MIN_LINES = 25
DEFAULT_MAX_BYTES = 9000


@dataclasses.dataclass
class Task:
    source_file: str        # repo-relative, forward slashes
    test_target: str        # repo-relative pytest target
    corruption: C.Corruption
    reference: str          # clean source (the ground truth)
    corrupted: str          # corrupted source the repairer sees
    fail_tail: str = ""     # pytest tail on the corrupted file (given to repairers)
    clean_secs: float = 0.0
    clean_n_passed: int = 0

    @property
    def id(self) -> str:
        return f"{self.source_file}::{self.corruption.id}"

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["corruption"] = self.corruption.to_dict()
        d["id"] = self.id
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(source_file=d["source_file"], test_target=d["test_target"],
                   corruption=C.Corruption.from_dict(d["corruption"]),
                   reference=d["reference"], corrupted=d["corrupted"],
                   fail_tail=d.get("fail_tail", ""), clean_secs=d.get("clean_secs", 0.0),
                   clean_n_passed=d.get("clean_n_passed", 0))


# --------------------------------------------------------------------------- io helpers

def read_text(path: pathlib.Path) -> str:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write_text(path: pathlib.Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def rel(repo: pathlib.Path, path: pathlib.Path) -> str:
    return path.resolve().relative_to(repo.resolve()).as_posix()


# --------------------------------------------------------------------------- test -> source mapping

def _module_to_path(repo: pathlib.Path, module: str) -> Optional[pathlib.Path]:
    parts = module.split(".")
    for n in range(len(parts), 0, -1):
        cand = repo.joinpath(*parts[:n]).with_suffix(".py")
        if cand.is_file():
            return cand
        pkg = repo.joinpath(*parts[:n], "__init__.py")
        if pkg.is_file() and n == len(parts):
            return None  # a package: not a single source file to corrupt
    return None


def sources_for_test(repo: pathlib.Path, test_target: str,
                     min_lines: int = DEFAULT_MIN_LINES, max_bytes: int = DEFAULT_MAX_BYTES) -> List[str]:
    """Repo source files a test module refers to, filtered by the size window."""
    tpath = repo / test_target
    src = read_text(tpath)
    tree = ast.parse(src)
    found: List[pathlib.Path] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                p = _module_to_path(repo, a.name)
                if p:
                    found.append(p)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            p = _module_to_path(repo, node.module)
            if p:
                found.append(p)
            else:
                for a in node.names:  # from pkg import module
                    p = _module_to_path(repo, node.module + "." + a.name)
                    if p:
                        found.append(p)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith(".py"):
            p = repo / node.value.replace("\\", "/")
            if p.is_file():
                found.append(p)
    out: List[str] = []
    seen = set()
    for p in found:
        try:
            r = rel(repo, p)
        except ValueError:
            continue
        if r in seen or r.startswith("tests/") or r == test_target or "__pycache__" in r:
            continue
        if r.startswith("tools/edit_fidelity/"):
            continue  # the harness never selects itself as a task
        seen.add(r)
        try:
            text = read_text(p)
        except (OSError, UnicodeDecodeError):
            continue
        if len(text.encode("utf-8")) > max_bytes or text.count("\n") < min_lines:
            continue
        try:
            ast.parse(text)
        except SyntaxError:
            continue
        out.append(r)
    return sorted(out)


def evaluator_targets(repo: pathlib.Path) -> List[str]:
    """The test files the repo's own required `pytest` context collects."""
    wf = repo / ".github" / "workflows" / "evaluator.yml"
    if not wf.is_file():
        return []
    seen: List[str] = []
    for t in re.findall(r"(tests/test_[A-Za-z0-9_]+\.py)", read_text(wf)):
        if t not in seen and (repo / t).is_file():
            seen.append(t)
    return seen


# --------------------------------------------------------------------------- the gate

@dataclasses.dataclass
class GateStats:
    pairs_considered: int = 0
    pairs_clean_fail: int = 0        # clean run not green (or zero collected) -> pair skipped
    pairs_clean_slow: int = 0        # clean run slower than the budget -> pair skipped
    candidates_tried: int = 0
    valid: int = 0
    discarded_green: int = 0         # corruption applied, tests still green  <-- coverage finding
    discarded_timeout: int = 0
    syntax_rejects: int = 0          # corrupt.py produced an unparseable splice (bug if > 0)
    by_class_tried: Dict[str, int] = dataclasses.field(default_factory=dict)
    by_class_valid: Dict[str, int] = dataclasses.field(default_factory=dict)
    by_class_green: Dict[str, int] = dataclasses.field(default_factory=dict)
    discarded_green_ids: List[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _bump(d: Dict[str, int], k: str) -> None:
    d[k] = d.get(k, 0) + 1


INFLIGHT = ".edit_fidelity_inflight.json"


class swapped_file:
    """Context manager: put ``text`` in place of ``source_file``, ALWAYS restore.

    A ``finally`` cannot run on a hard kill, and a killed run once left a
    corrupted module in the working tree (measured). So the reference bytes are
    written to ``<repo>/.edit_fidelity_inflight.json`` BEFORE the swap and
    removed AFTER the verified restore; ``recover_inflight`` replays it at the
    next start-up.
    """

    def __init__(self, repo: pathlib.Path, source_file: str, reference: str, text: str):
        self.repo, self.source_file, self.reference, self.text = pathlib.Path(repo), source_file, reference, text
        self.path = self.repo / source_file
        self.marker = self.repo / INFLIGHT

    def __enter__(self):
        self.marker.write_text(json.dumps({"source_file": self.source_file, "reference": self.reference}),
                               encoding="utf-8")
        write_text(self.path, self.text)
        return self.path

    def __exit__(self, *exc):
        write_text(self.path, self.reference)
        M.purge_pyc(self.path)
        if read_text(self.path) != self.reference:
            raise RuntimeError(f"restore of {self.source_file} is not byte-identical -- aborting")
        try:
            self.marker.unlink()
        except OSError:
            pass
        return False


def recover_inflight(repo: pathlib.Path) -> Optional[str]:
    """Restore a file a killed run left swapped. Returns the file restored, or None."""
    marker = pathlib.Path(repo) / INFLIGHT
    if not marker.is_file():
        return None
    d = json.loads(marker.read_text(encoding="utf-8"))
    path = pathlib.Path(repo) / d["source_file"]
    if path.is_file() and read_text(path) != d["reference"]:
        write_text(path, d["reference"])
        M.purge_pyc(path)
    marker.unlink()
    return d["source_file"]


def gate_one(repo: pathlib.Path, source_file: str, test_target: str, corruption: C.Corruption,
             reference: str, timeout: int = 300, python: Optional[str] = None) -> Tuple[bool, dict]:
    """Apply one corruption in place, run the target, ALWAYS restore. Returns (fails, run)."""
    with swapped_file(repo, source_file, reference, corruption.apply(reference)) as path:
        run = M.pass_at_1(repo, test_target, touched=[path], timeout=timeout, python=python)
    return (not run["passed"]), run


def select_tasks(repo: os.PathLike, pairs: Sequence[Tuple[str, str]], n_target: int, seed: int = 0,
                 max_per_file: int = 3, max_candidates_per_file: int = 12,
                 clean_budget_secs: float = 30.0, timeout: int = 300, python: Optional[str] = None,
                 classes: Optional[List[str]] = None, log=print) -> Tuple[List[Task], GateStats]:
    """Walk (source_file, test_target) pairs until ``n_target`` VALID tasks exist.

    Candidate order inside a file is seeded (corrupt.enumerate_candidates);
    pair order is the seeded shuffle of ``pairs``. Everything tried is counted
    in the returned GateStats.
    """
    repo = pathlib.Path(repo)
    rng = random.Random(seed)
    order = list(pairs)
    rng.shuffle(order)
    stats = GateStats()
    tasks: List[Task] = []
    clean_cache: Dict[str, dict] = {}
    for source_file, test_target in order:
        if len(tasks) >= n_target:
            break
        stats.pairs_considered += 1
        path = repo / source_file
        reference = read_text(path)
        # 1. clean run must be green with >= 1 collected test
        if test_target not in clean_cache:
            clean_cache[test_target] = M.pass_at_1(repo, test_target, touched=[path], timeout=timeout, python=python)
        clean = clean_cache[test_target]
        if not clean["passed"]:
            stats.pairs_clean_fail += 1
            log(f"  skip pair (clean not green: rc={clean['rc']} passed={clean['n_passed']}) {test_target}")
            continue
        if clean["secs"] > clean_budget_secs:
            stats.pairs_clean_slow += 1
            log(f"  skip pair (clean run {clean['secs']}s > budget) {test_target}")
            continue
        # 2. enumerate corruptions in a seeded order
        file_seed = rng.randint(0, 2**31 - 1)
        cands, cstats = C.enumerate_candidates(reference, source_file, seed=file_seed, classes=classes)
        stats.syntax_rejects += cstats["syntax_rejects"]
        got_here = 0
        for cand in cands[:max_candidates_per_file]:
            if len(tasks) >= n_target or got_here >= max_per_file:
                break
            stats.candidates_tried += 1
            _bump(stats.by_class_tried, cand.cls)
            fails, run = gate_one(repo, source_file, test_target, cand, reference, timeout=timeout, python=python)
            if run["rc"] == -9:
                stats.discarded_timeout += 1
                log(f"  timeout  {source_file}::{cand.id}")
                continue
            if not fails:
                stats.discarded_green += 1
                _bump(stats.by_class_green, cand.cls)
                stats.discarded_green_ids.append(f"{source_file}::{cand.id}")
                log(f"  GREEN    {source_file}::{cand.id}  ({cand.note}) -- suite cannot see it, discarded")
                continue
            stats.valid += 1
            got_here += 1
            _bump(stats.by_class_valid, cand.cls)
            tasks.append(Task(source_file=source_file, test_target=test_target, corruption=cand,
                              reference=reference, corrupted=cand.apply(reference),
                              fail_tail=run["tail"], clean_secs=clean["secs"], clean_n_passed=clean["n_passed"]))
            log(f"  VALID    {source_file}::{cand.id}  ({cand.note}) via {test_target} [{run['secs']}s]")
    return tasks, stats


def build_pairs(repo: os.PathLike, test_targets: Iterable[str],
                min_lines: int = DEFAULT_MIN_LINES, max_bytes: int = DEFAULT_MAX_BYTES) -> List[Tuple[str, str]]:
    repo = pathlib.Path(repo)
    pairs: List[Tuple[str, str]] = []
    for t in test_targets:
        if not (repo / t).is_file():
            continue
        for s in sources_for_test(repo, t, min_lines=min_lines, max_bytes=max_bytes):
            pairs.append((s, t))
    return pairs


def save_tasks(path: pathlib.Path, tasks: List[Task], stats: GateStats, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "gate": stats.to_dict(), "tasks": [t.to_dict() for t in tasks]}
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")


def load_tasks(path: pathlib.Path) -> Tuple[List[Task], dict, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [Task.from_dict(d) for d in payload["tasks"]], payload.get("gate", {}), payload.get("meta", {})
