"""Deterministic, seeded, AST-located source corruption with a KNOWN inverse.

Every corruption is a single contiguous splice of the source text:

    corrupted = source[:start] + replacement + source[start + len(original):]

so the untouched remainder of the file is preserved BYTE-FOR-BYTE. That
invariant is the point of the module: a corruption that reformatted the file
would make every downstream fidelity metric meaningless, because the
"reference" the repair is scored against would no longer be the file the
repairer saw. The inverse is the same splice run backwards (``invert``), and
``apply`` / ``invert`` are checked against each other in the tests.

The AST is used only to LOCATE nodes (lineno / col_offset / end_lineno /
end_col_offset, available since Python 3.8). The text is never re-emitted with
``ast.unparse`` here -- unparse drops comments and normalises quoting, which
would violate the byte-for-byte rule.

Corruption classes (all recorded with file, lineno, col, original, corrupted):

    comparison_flip   ``<`` <-> ``<=``, ``>`` <-> ``>=``, ``==`` <-> ``!=``
    boundary_shift    plain decimal int literal n -> n+1 or n-1 (seeded)
    boolop_flip       ``and`` <-> ``or`` (first operator of the BoolOp)
    arg_swap          swap two ADJACENT positional call arguments
    guard_drop        remove an ``if <test>:`` line, keep (dedented) body
    unary_not_drop    ``not x`` -> ``x`` (parentheses of the operand kept)

Candidates are enumerated only INSIDE function bodies (def / async def), so
every task has an enclosing function for the sloppy repairer to rewrite and
for a model to be told about. Module-level and class-level statements are
deliberately out of scope.

Several DISJOINT corruptions can be carried by one task (``apply_all`` /
``invert_all``), which is how the hard task shape builds a bug that no
single-hunk edit can repair. Disjointness is enforced, not assumed, and the
round trip ``invert_all(apply_all(src, cs), cs) == src`` is exact -- the whole
multi-node machinery is still only splices, so the byte-for-byte rule above
holds for every character outside the union of the spans.

Adding a class: write ``_find_<name>(source, tree, rng) -> list[Corruption]``,
register it in ``CORRUPTION_CLASSES``. Each candidate must be a single splice
that leaves the file parseable (``enumerate_candidates`` re-parses and drops
any that are not, counting them under ``syntax_rejects``).

stdlib only. See README.md for why that is not negotiable.
"""
from __future__ import annotations

import ast
import dataclasses
import random
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

CORRUPTION_CLASS_NAMES = (
    "comparison_flip",
    "boundary_shift",
    "boolop_flip",
    "arg_swap",
    "guard_drop",
    "unary_not_drop",
)

_CMP_FLIP: Dict[str, str] = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "=="}
_CMP_OP_TEXT = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!="}
_CMP_RE = re.compile(r"<=|>=|==|!=|<|>")


@dataclasses.dataclass(frozen=True)
class Corruption:
    """One splice. ``start`` is a CHARACTER offset into the source string."""

    cls: str
    file: str
    lineno: int
    col: int
    start: int
    original: str
    corrupted: str
    func: str  # qualified name of the enclosing function (for prompts/sloppy)
    func_lineno: int
    func_end_lineno: int
    note: str = ""

    @property
    def id(self) -> str:
        return f"{self.cls}@{self.lineno}:{self.col}"

    @property
    def func_lines(self) -> int:
        """Length, in lines, of the enclosing function (the `def` line included).

        The hard task shape prefers long enclosing functions: a one-line
        function localises the bug for the model by simply existing.
        """
        return self.func_end_lineno - self.func_lineno + 1

    @property
    def end(self) -> int:
        """Character offset one past this splice's span in the REFERENCE text."""
        return self.start + len(self.original)

    def apply(self, source: str) -> str:
        seg = source[self.start:self.start + len(self.original)]
        if seg != self.original:
            raise ValueError(f"{self.id}: source no longer matches recorded original at {self.start}")
        return source[:self.start] + self.corrupted + source[self.start + len(self.original):]

    def invert(self, corrupted_source: str) -> str:
        seg = corrupted_source[self.start:self.start + len(self.corrupted)]
        if seg != self.corrupted:
            raise ValueError(f"{self.id}: corrupted source does not carry the recorded splice")
        return corrupted_source[:self.start] + self.original + corrupted_source[self.start + len(self.corrupted):]

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Corruption":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


# --------------------------------------------------------------------------- spans

class _LineIndex:
    """Map (lineno, byte col_offset) -> character offset, once per source."""

    def __init__(self, source: str):
        self.lines = source.splitlines(keepends=True)
        self.starts: List[int] = []
        pos = 0
        for ln in self.lines:
            self.starts.append(pos)
            pos += len(ln)
        self.starts.append(pos)

    def offset(self, lineno: int, col_bytes: int) -> int:
        line = self.lines[lineno - 1] if lineno - 1 < len(self.lines) else ""
        # ast col offsets are UTF-8 BYTE offsets; convert to characters.
        col_chars = len(line.encode("utf-8")[:col_bytes].decode("utf-8", errors="ignore"))
        return self.starts[lineno - 1] + col_chars

    def span(self, node: ast.AST) -> Tuple[int, int]:
        return (self.offset(node.lineno, node.col_offset),
                self.offset(node.end_lineno, node.end_col_offset))


def _enclosing_functions(tree: ast.AST):
    """Yield (funcdef, qualname) for every def/async def, with parent links set."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._ef_parent = node  # type: ignore[attr-defined]

    def qual(fn: ast.AST) -> str:
        parts = []
        cur = fn
        while cur is not None and not isinstance(cur, ast.Module):
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                parts.append(cur.name)
            cur = getattr(cur, "_ef_parent", None)
        return ".".join(reversed(parts))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node, qual(node)


def _innermost_function(node: ast.AST) -> Optional[ast.AST]:
    cur = getattr(node, "_ef_parent", None)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur
        cur = getattr(cur, "_ef_parent", None)
    return None


def _in_fstring(node: ast.AST) -> bool:
    cur = getattr(node, "_ef_parent", None)
    while cur is not None:
        if isinstance(cur, ast.JoinedStr):
            return True
        cur = getattr(cur, "_ef_parent", None)
    return False


# --------------------------------------------------------------------------- finders

def _find_comparison_flip(source: str, tree: ast.AST, idx: _LineIndex, rng: random.Random,
                          file: str, funcs: dict) -> List[Corruption]:
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        op_text = _CMP_OP_TEXT.get(type(node.ops[0]))
        if op_text is None or _in_fstring(node):
            continue
        fn = _innermost_function(node)
        if fn is None:
            continue
        gap_start = idx.span(node.left)[1]
        gap_end = idx.span(node.comparators[0])[0]
        gap = source[gap_start:gap_end]
        if "#" in gap:
            continue
        m = None
        for cand in _CMP_RE.finditer(gap):
            if cand.group(0) == op_text:
                m = cand
                break
        if m is None:
            continue
        start = gap_start + m.start()
        out.append(Corruption("comparison_flip", file, node.lineno, node.col_offset, start,
                              op_text, _CMP_FLIP[op_text], funcs[fn][0], fn.lineno, fn.end_lineno,
                              note=f"{op_text} -> {_CMP_FLIP[op_text]}"))
    return out


def _find_boundary_shift(source: str, tree: ast.AST, idx: _LineIndex, rng: random.Random,
                         file: str, funcs: dict) -> List[Corruption]:
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or type(node.value) is not int:
            continue
        if _in_fstring(node):
            continue
        fn = _innermost_function(node)
        if fn is None:
            continue
        s, e = idx.span(node)
        seg = source[s:e]
        if not re.fullmatch(r"\d+", seg):
            continue  # hex/octal/underscored/complex literals: leave alone
        delta = rng.choice((1, -1))
        new = node.value + delta
        if new < 0:
            new = node.value + 1
            delta = 1
        out.append(Corruption("boundary_shift", file, node.lineno, node.col_offset, s,
                              seg, str(new), funcs[fn][0], fn.lineno, fn.end_lineno,
                              note=f"{seg} -> {new} ({delta:+d})"))
    return out


def _find_boolop_flip(source: str, tree: ast.AST, idx: _LineIndex, rng: random.Random,
                      file: str, funcs: dict) -> List[Corruption]:
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BoolOp) or _in_fstring(node):
            continue
        fn = _innermost_function(node)
        if fn is None:
            continue
        word = "and" if isinstance(node.op, ast.And) else "or"
        other = "or" if word == "and" else "and"
        gap_start = idx.span(node.values[0])[1]
        gap_end = idx.span(node.values[1])[0]
        gap = source[gap_start:gap_end]
        if "#" in gap:
            continue
        m = re.search(rf"\b{word}\b", gap)
        if m is None:
            continue
        start = gap_start + m.start()
        out.append(Corruption("boolop_flip", file, node.lineno, node.col_offset, start,
                              word, other, funcs[fn][0], fn.lineno, fn.end_lineno,
                              note=f"{word} -> {other}"))
    return out


def _find_arg_swap(source: str, tree: ast.AST, idx: _LineIndex, rng: random.Random,
                   file: str, funcs: dict) -> List[Corruption]:
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2 or _in_fstring(node):
            continue
        if any(isinstance(a, ast.Starred) for a in node.args):
            continue
        fn = _innermost_function(node)
        if fn is None:
            continue
        pairs = list(range(len(node.args) - 1))
        rng.shuffle(pairs)
        for i in pairs:
            a, b = node.args[i], node.args[i + 1]
            (sa, ea), (sb, eb) = idx.span(a), idx.span(b)
            seg_a, seg_b, between = source[sa:ea], source[sb:eb], source[ea:sb]
            if seg_a == seg_b or "#" in between or "\n" in between:
                continue
            # Keyword-style or generator args are not plain positionals to swap.
            if isinstance(a, ast.GeneratorExp) or isinstance(b, ast.GeneratorExp):
                continue
            original = source[sa:eb]
            corrupted = seg_b + between + seg_a
            out.append(Corruption("arg_swap", file, node.lineno, node.col_offset, sa,
                                  original, corrupted, funcs[fn][0], fn.lineno, fn.end_lineno,
                                  note=f"args {i} <-> {i + 1}"))
            break
    return out


def _find_guard_drop(source: str, tree: ast.AST, idx: _LineIndex, rng: random.Random,
                     file: str, funcs: dict) -> List[Corruption]:
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or node.orelse:
            continue
        parent = getattr(node, "_ef_parent", None)
        if isinstance(parent, ast.If) and node in parent.orelse:
            continue  # this is an elif arm
        fn = _innermost_function(node)
        if fn is None:
            continue
        body0 = node.body[0]
        if body0.lineno == node.lineno:
            continue  # one-liner `if x: return` -- no separate body line to keep
        if node.test.end_lineno != node.lineno:
            continue  # multi-line test: dropping line 1 alone would not parse
        if_col = node.col_offset
        body_col = body0.col_offset
        delta = body_col - if_col
        if delta <= 0:
            continue
        s, e = idx.span(node)
        # Extend the splice back to the start of the `if` line's indentation so the
        # kept body lands at the `if`'s own column.
        line_start = idx.starts[node.lineno - 1]
        s = line_start
        original = source[s:e]
        lines = original.splitlines(keepends=True)
        body_lines = lines[1:]
        pad = " " * delta
        kept = []
        for ln in body_lines:
            if ln.startswith(pad):
                kept.append(ln[delta:])
            elif ln.strip() == "":
                kept.append(ln)
            else:
                kept = None  # mixed indentation / tabs: not a safe dedent
                break
        if not kept:
            continue
        corrupted = "".join(kept)
        out.append(Corruption("guard_drop", file, node.lineno, node.col_offset, s,
                              original, corrupted, funcs[fn][0], fn.lineno, fn.end_lineno,
                              note=f"dropped `{lines[0].strip()}`"))
    return out


def _find_unary_not_drop(source: str, tree: ast.AST, idx: _LineIndex, rng: random.Random,
                         file: str, funcs: dict) -> List[Corruption]:
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.UnaryOp) or not isinstance(node.op, ast.Not):
            continue
        if _in_fstring(node):
            continue
        fn = _innermost_function(node)
        if fn is None:
            continue
        s, e = idx.span(node)
        seg = source[s:e]
        m = re.match(r"not\s*", seg)
        if m is None or m.end() >= len(seg):
            continue
        out.append(Corruption("unary_not_drop", file, node.lineno, node.col_offset, s,
                              seg, seg[m.end():], funcs[fn][0], fn.lineno, fn.end_lineno,
                              note="dropped `not`"))
    return out


CORRUPTION_CLASSES: Dict[str, Callable] = {
    "comparison_flip": _find_comparison_flip,
    "boundary_shift": _find_boundary_shift,
    "boolop_flip": _find_boolop_flip,
    "arg_swap": _find_arg_swap,
    "guard_drop": _find_guard_drop,
    "unary_not_drop": _find_unary_not_drop,
}


# --------------------------------------------------------------------------- api

def enumerate_candidates(source: str, file: str, seed: int = 0,
                         classes: Optional[List[str]] = None, min_func_lines: int = 0,
                         prefer_long: bool = False) -> Tuple[List[Corruption], dict]:
    """All corruption candidates in ``source``, in a seeded shuffled order.

    Returns (candidates, stats). ``stats["syntax_rejects"]`` counts candidates
    whose splice did not re-parse (should be 0; non-zero is a bug in a finder).
    Every returned candidate has been verified to (a) re-parse and (b) invert
    back to the exact original string.

    ``min_func_lines`` drops candidates whose enclosing function is shorter than
    that (counted in ``stats["short_func_rejects"]``), and ``prefer_long`` then
    orders what survives by enclosing-function length, longest first. Both are
    off by default, so the easy shape enumerates exactly as it always did. They
    are the "longer enclosing functions" lever of the hard shape: the shorter
    the function a bug sits in, the more the function boundary itself tells the
    repairer where to look. The sort is STABLE, so the seeded shuffle survives
    as the tie-break among functions of equal length and the order stays
    reproducible from the seed.
    """
    tree = ast.parse(source)
    idx = _LineIndex(source)
    funcs = {fn: (name,) for fn, name in _enclosing_functions(tree)}
    rng = random.Random(seed)
    wanted = list(classes or CORRUPTION_CLASS_NAMES)
    raw: List[Corruption] = []
    for name in wanted:
        raw.extend(CORRUPTION_CLASSES[name](source, tree, idx, rng, file, funcs))
    stats = {"enumerated": len(raw), "syntax_rejects": 0, "noop_rejects": 0,
             "short_func_rejects": 0, "by_class": {}}
    ok: List[Corruption] = []
    for c in raw:
        if c.original == c.corrupted:
            stats["noop_rejects"] += 1
            continue
        if c.func_lines < min_func_lines:
            stats["short_func_rejects"] += 1
            continue
        try:
            corrupted = c.apply(source)
            ast.parse(corrupted)
        except (SyntaxError, ValueError):
            stats["syntax_rejects"] += 1
            continue
        if c.invert(corrupted) != source:
            stats["syntax_rejects"] += 1
            continue
        ok.append(c)
    rng.shuffle(ok)
    if prefer_long:
        ok.sort(key=lambda c: -c.func_lines)  # stable: the seeded shuffle is the tie-break
    for c in ok:
        stats["by_class"][c.cls] = stats["by_class"].get(c.cls, 0) + 1
    stats["func_lines_max"] = max((c.func_lines for c in ok), default=0)
    return ok, stats


def untouched_remainder_identical(source: str, corrupted: str, c: Corruption) -> bool:
    """True iff everything outside the splice is byte-identical (the invariant)."""
    return (source[:c.start] == corrupted[:c.start]
            and source[c.start + len(c.original):] == corrupted[c.start + len(c.corrupted):])


# --------------------------------------------------------------------------- multi-node

def spans_disjoint(corruptions: Sequence[Corruption]) -> bool:
    """True iff no two splices overlap in the reference text.

    ``apply_all`` is only well defined on disjoint splices: two corruptions that
    share a character would each record an ``original`` the other has already
    rewritten, and the second ``apply`` would (correctly) refuse.
    """
    spans = sorted((c.start, c.end) for c in corruptions)
    return all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))


def apply_all(source: str, corruptions: Sequence[Corruption]) -> str:
    """Apply several disjoint splices to one source, HIGHEST ``start`` first.

    Descending order needs no offset bookkeeping: when a splice is applied,
    every character below its own ``start`` is still byte-identical to
    ``source``, so its recorded ``(start, original)`` pair is still exact. Each
    ``apply`` re-checks that pair, so a violation raises instead of silently
    corrupting the wrong span.
    """
    if not spans_disjoint(corruptions):
        raise ValueError("overlapping corruptions cannot be applied together")
    for c in sorted(corruptions, key=lambda c: c.start, reverse=True):
        source = c.apply(source)
    return source


def invert_all(corrupted: str, corruptions: Sequence[Corruption]) -> str:
    """Exact inverse of ``apply_all``, LOWEST ``start`` first.

    The mirror of the argument above: once every splice below this one has been
    undone, the text under ``start`` is again byte-identical to the reference,
    so the recorded offset is exact. ``apply_all`` then ``invert_all`` returns
    the original string character for character -- the property the oracle arm
    depends on, and one the tests assert directly.
    """
    for c in sorted(corruptions, key=lambda c: c.start):
        corrupted = c.invert(corrupted)
    return corrupted


def untouched_remainder_identical_multi(source: str, corrupted: str,
                                        corruptions: Sequence[Corruption]) -> bool:
    """True iff every character outside the union of the splices is unchanged.

    The multi-node form of the byte-for-byte invariant: walk the reference and
    the corrupted text in step, skipping each recorded span on its own side.
    """
    cs = sorted(corruptions, key=lambda c: c.start)
    ref_pos = out_pos = 0
    for c in cs:
        if source[ref_pos:c.start] != corrupted[out_pos:out_pos + (c.start - ref_pos)]:
            return False
        out_pos += c.start - ref_pos
        ref_pos = c.end
        out_pos += len(c.corrupted)
    return source[ref_pos:] == corrupted[out_pos:]
