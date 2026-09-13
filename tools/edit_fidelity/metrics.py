"""Edit-fidelity metrics. stdlib only (see README.md: FU-118 is why).

fidelity_lev(reference, output)
    Levenshtein(reference, output) / len(reference), measured after both sides
    are normalised to LF line endings (see normalize_eol for the measured
    reason). 0.0 means the repair is byte-identical to the known-good original
    up to line endings; larger means the repairer rewrote more than the bug.
    This is "faithful to the original implementation" made numeric.
    Levenshtein is exact (two-row DP) after
    stripping the common prefix and suffix, which is what makes it tractable
    on whole files in pure Python: a minimal edit leaves a tiny middle.

cognitive_complexity(src)
    Whole-module cognitive complexity. RULE SET IMPLEMENTED (this is a
    documented SUBSET of SonarSource's Cognitive Complexity, G. Ann Campbell
    2017, with one deliberate deviation, and is not the reference
    implementation):

      structural, +1 AND nesting increment (+ current nesting level)
          if, for, async for, while, except handler, if-expression (ternary),
          with / async with   <-- DEVIATION: Sonar does not score `with`;
                                  this harness does, per its spec, because a
                                  repairer that wraps a body in a context
                                  manager it did not need is exactly the
                                  over-edit being measured.
      hybrid, +1, NO nesting increment
          elif (an If that is the sole statement of a parent If's orelse),
          else (of if/for/while)
      +1 per sequence of like boolean operators
          `a and b and c` -> +1 ; `a and b or c` -> +2 (operator changed)
      nesting-only (raises the nesting level of what they contain, no +1)
          a def / async def / lambda NESTED inside a function. Top-level
          functions and methods start at nesting 0; classes are transparent.
      NOT scored (deviations from Sonar, stated so nobody overclaims):
          recursion (+1 per recursive cycle in Sonar), `finally`, `match`,
          comprehension for/if clauses, `break`/`continue`, `assert`.

delta_cc(reference, output)
    cognitive_complexity(output) - cognitive_complexity(reference).

pass_at_1(repo, test_target, touched=())
    Run the task's own pytest target as a SUBPROCESS (never in-process, so a
    corrupted module can never be served from this interpreter's import
    cache). Returns a dict with ``passed`` (True only if rc == 0 AND at least
    one test was collected), the rc, counts, duration and the output tail.
    ``touched`` names source files whose ``__pycache__`` entry is purged
    first: a same-size edit written within the same second would otherwise
    validate against a stale .pyc (pyc headers carry int mtime + size), and
    the run would silently test the OLD code.
"""
from __future__ import annotations

import ast
import os
import pathlib
import re
import subprocess
import sys
import time
from typing import Dict, Iterable, Optional


# --------------------------------------------------------------------------- levenshtein

def levenshtein(a: str, b: str) -> int:
    """Exact edit distance (insert/delete/substitute, unit cost)."""
    if a == b:
        return 0
    # strip common prefix / suffix -- exact, and it is what makes whole files cheap
    i = 0
    n, m = len(a), len(b)
    while i < n and i < m and a[i] == b[i]:
        i += 1
    j = 0
    while j < n - i and j < m - i and a[n - 1 - j] == b[m - 1 - j]:
        j += 1
    a = a[i:n - j]
    b = b[i:m - j]
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for x, ca in enumerate(a, 1):
        cur = [x]
        for y, cb in enumerate(b, 1):
            cur.append(min(prev[y] + 1, cur[y - 1] + 1, prev[y - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def normalize_eol(text: str) -> str:
    """CRLF/CR -> LF. A Windows checkout (core.autocrlf=true) hands the repairer
    CRLF files and models answer in LF; measured 2026-09-12, that alone scored
    every byte-perfect repair at fidelity 0.022 (= lines / chars). Line endings
    are a checkout artifact, not an edit, so fidelity is measured LF-to-LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def fidelity_lev(reference: str, output: str) -> float:
    reference, output = normalize_eol(reference), normalize_eol(output)
    if not reference:
        raise ValueError("reference is empty; fidelity is undefined")
    return levenshtein(reference, output) / len(reference)


# --------------------------------------------------------------------------- cognitive complexity

class _CC(ast.NodeVisitor):
    def __init__(self):
        self.total = 0
        self.nesting = 0
        self.fn_depth = 0

    # -- helpers
    def _structural(self, node, fields_nested, fields_flat=()):
        self.total += 1 + self.nesting
        self.nesting += 1
        for f in fields_nested:
            for child in getattr(node, f, []) or []:
                self.visit(child)
        self.nesting -= 1
        for f in fields_flat:
            for child in getattr(node, f, []) or []:
                self.visit(child)

    def _visit_else(self, stmts):
        if stmts:
            self.total += 1
            for s in stmts:
                self.visit(s)

    # -- structural
    def visit_If(self, node, _is_elif=False):
        if _is_elif:
            self.total += 1          # hybrid: +1, no nesting increment
        else:
            self.total += 1 + self.nesting
        self.visit(node.test)
        self.nesting += 1
        for s in node.body:
            self.visit(s)
        self.nesting -= 1
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            self.visit_If(node.orelse[0], _is_elif=True)
        else:
            self._visit_else(node.orelse)

    def _loop(self, node):
        self.total += 1 + self.nesting
        for f in ("target", "iter", "test"):
            child = getattr(node, f, None)
            if child is not None:
                self.visit(child)
        self.nesting += 1
        for s in node.body:
            self.visit(s)
        self.nesting -= 1
        self._visit_else(node.orelse)

    visit_For = _loop
    visit_AsyncFor = _loop
    visit_While = _loop

    def visit_ExceptHandler(self, node):
        self._structural(node, ("body",))

    def visit_Try(self, node):
        for s in node.body:
            self.visit(s)
        for h in node.handlers:
            self.visit(h)
        for s in node.orelse:      # try/else and finally: not scored (documented)
            self.visit(s)
        for s in node.finalbody:
            self.visit(s)

    visit_TryStar = visit_Try

    def _with(self, node):
        for item in node.items:
            self.visit(item)
        self._structural(node, ("body",))

    visit_With = _with
    visit_AsyncWith = _with

    def visit_IfExp(self, node):
        self.total += 1 + self.nesting
        self.visit(node.test)
        self.nesting += 1
        self.visit(node.body)
        self.visit(node.orelse)
        self.nesting -= 1

    def visit_BoolOp(self, node):
        self.total += 1              # one sequence of like operators
        for v in node.values:
            self.visit(v)

    # -- nesting only: a def INSIDE a def (or a lambda inside one) raises the
    # nesting level of what it contains; a top-level function or a method starts
    # at nesting 0 (Sonar scores each method from zero); classes are transparent.
    def _fn(self, node):
        self.fn_depth += 1
        nested = self.fn_depth > 1
        if nested:
            self.nesting += 1
        self.generic_visit(node)
        if nested:
            self.nesting -= 1
        self.fn_depth -= 1

    visit_FunctionDef = _fn
    visit_AsyncFunctionDef = _fn
    visit_Lambda = _fn


def cognitive_complexity(src: str) -> int:
    """Whole-module cognitive complexity under the rule set in the module docstring.

    A module of plain functions scores exactly the sum of its functions' scores
    (each starting at nesting 0), plus any module-level control flow, which is
    how the metric is conventionally reported.
    """
    v = _CC()
    v.visit(ast.parse(src))
    return v.total


def delta_cc(reference: str, output: str) -> int:
    return cognitive_complexity(output) - cognitive_complexity(reference)


# --------------------------------------------------------------------------- pass@1

_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|error|errors|skipped|deselected|xfailed|xpassed)")


def purge_pyc(path: pathlib.Path) -> int:
    """Delete every __pycache__ entry for ``path``. Returns the count removed."""
    cache = path.parent / "__pycache__"
    n = 0
    if cache.is_dir():
        for pyc in cache.glob(path.stem + ".*.pyc"):
            try:
                pyc.unlink()
                n += 1
            except OSError:
                pass
    return n


def pass_at_1(repo: os.PathLike, test_target: str, touched: Iterable[os.PathLike] = (),
              timeout: int = 300, python: Optional[str] = None, extra_args: Iterable[str] = ()) -> Dict:
    """Run ``python -m pytest <test_target>`` as a subprocess inside ``repo``."""
    repo = pathlib.Path(repo)
    for t in touched:
        purge_pyc(pathlib.Path(t))
    cmd = [python or sys.executable, "-m", "pytest", test_target, "-q", "-x",
           "-p", "no:cacheprovider", "--no-header", *extra_args]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True,
                           timeout=timeout, env=env, encoding="utf-8", errors="replace")
        rc, out = p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        rc = -9
        out = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) \
            + f"\n[pass_at_1] TIMEOUT after {timeout}s"
    dur = round(time.time() - t0, 2)
    counts = {}
    for num, kind in _SUMMARY_RE.findall(out):
        counts[kind] = counts.get(kind, 0) + int(num)
    n_pass = counts.get("passed", 0)
    passed = (rc == 0 and n_pass > 0)
    tail_lines = out.strip().splitlines()
    return {
        "passed": passed,
        "rc": rc,
        "n_passed": n_pass,
        "n_failed": counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0),
        "collected_zero": (rc == 5 or (rc == 0 and n_pass == 0)),
        "secs": dur,
        "tail": "\n".join(tail_lines[-60:]),
        "cmd": cmd,
    }
