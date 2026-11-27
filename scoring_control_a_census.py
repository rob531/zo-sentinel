"""scoring_control_a_census.py -- report-only Control-(a) ratchet for the
scoring verdict/tier consumers (the product moat).

WHY: the SFT verdict path derives a published risk tier/verdict from an
``overall_risk`` (or axis) LABEL. The pre-committed anti-vanity Control-(a) is:
a *degenerate / unrecognized* label is corrupt input -> UNKNOWN / conservative
tier, never a crash and never a verbatim-garbage verdict (see trust_gating_override
.trust_gate's degenerate-input guard, landed #16267). The repo grows dozens of
near-duplicate tier-deriving consumers (autobuilder churn), so the real risk is
REGRESSION: a *new* consumer that indexes an ordered risk vocabulary by a
label-derived value WITHOUT a guard (``ORDER.index(lbl)`` / ``MAP[lbl]``) will
``ValueError``/``KeyError`` on a degenerate label -- a Control-(a) bypass.

This tool is a RATCHET, not a point-fix:
  - it enumerates ordered risk vocabularies (``*_ORDER`` / ``*SEVERITY*`` /
    ``*TIER*`` lists & label->tier dict maps, detected by VALUE so a renamed
    copy can't hide) and the danger sites that index/subscript them by a
    non-literal value;
  - it classifies each danger site GUARDED (inside try/except Value|Key|Lookup
    |Exception, or an enclosing ``if x in VOCAB`` membership guard) vs UNGUARDED
    (conservative: any guard evidence -> GUARDED, so the author never over-fires;
    the adversary catches under-firing);
  - ``--check`` REDs (rc=1) only on a NEW file or an INCREASED unguarded count
    vs the pinned baseline; an unparseable tracked file is UNKNOWN (rc=2), never
    silently clean (Control-(a) on the tool itself).

Pure stdlib (ast + git ls-files). No app/db/network imports at module scope, so
it is CI-safe and bare-runnable on the Tower. Report-only: it never mutates
source and never blocks a merge by itself (advisory gate).

SCOPED LIMITATIONS (honest under-coverage -- the claim is "no NEW unguarded site
of a SHAPE I detect", not "all Control-(a) bypasses"; a frontier adversary review
surfaced these, kept here rather than hidden):
  - Vocabularies bound by a dict/enumerate COMPREHENSION ({t: i for i, t in
    enumerate(ORDER)}) are not detected -- only literal list/tuple/set/dict
    assignments are. Such rank-maps exist and are today safe-by-construction via
    normalizers, but the ratchet cannot fence that shape yet.
  - The baseline keys on a per-file UNGUARDED COUNT, so a within-file swap (cure
    one site, add a new unguarded one in the same file) is not caught; and a
    baselined count may include safe-by-construction sites (an unguarded subscript
    whose key provably iterates a literal subset of the vocab). Baseline debt is
    "watch, don't worsen", NOT "each entry is a live bug".
  - The membership guard is function-scoped and subject-agnostic (any `x in VOCAB`
    anywhere in the enclosing function marks the site GUARDED) -- it errs toward
    GUARDED (fewer false alarms) by design, so a contrived mismatched guard could
    mask a site. Acceptable for a report-only advisory; not a blocking gate.
Detected crash shapes ARE: VOCAB.index(x), MAP[x] (Load), MAP[x] OP= y (AugAssign
reads first), del MAP[x], MAP.pop(x) (1-arg, no default).
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from typing import Dict, List, Optional, Set, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, "scoring_control_a_census_baseline.json")

# Risk / tier tokens. A list/tuple/dict literal is treated as an ordered risk
# vocabulary when a MAJORITY of its string elements (or dict keys) are these.
RISK_TOKENS = frozenset({
    "LOW", "MEDIUM", "HIGH", "CRITICAL", "INFO", "UNKNOWN",
    "DANGER", "NOMINAL", "SAFE", "CAUTION", "SEVERE", "MODERATE",
    "HIGH_RISK", "HIGH_RISK_ISOLATED", "CAUTION_LIMITED", "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED", "KNOWN_THREAT",
    "INSUFFICIENT", "UNASSESSED", "HIGH_RISK", "MINIMAL",
})
# Exceptions that make an index/subscript safe when caught.
SAFE_EXC = frozenset({"ValueError", "KeyError", "IndexError", "LookupError", "Exception"})
# Path prefixes that are not production consumers.
EXEMPT_PREFIXES = ("tests/", "test/")
EXEMPT_BASENAMES = ("scoring_control_a_census.py",)


# ----------------------------------------------------------------------------- git

def _repo_root(start: str) -> str:
    try:
        out = subprocess.run(["git", "-C", start, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return start


def _tracked_py(root: str) -> List[str]:
    """git-tracked *.py as repo-relative POSIX paths (NUL-delimited: C-quoted /
    non-ASCII names stay in scope -- a path the shell would mangle is still audited)."""
    out = subprocess.run(["git", "-C", root, "ls-files", "-z", "--", "*.py"],
                         capture_output=True, text=True, check=True)
    return [p for p in out.stdout.split("\0") if p]


def _is_exempt(rel: str) -> bool:
    if any(rel == b or rel.endswith("/" + b) for b in EXEMPT_BASENAMES):
        return True
    return any(rel.startswith(p) for p in EXEMPT_PREFIXES)


# ----------------------------------------------------------------------------- ast

def _str_elems(node: ast.AST) -> List[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [e.value for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if isinstance(node, ast.Dict):
        return [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    return []


def _is_risk_vocab(node: ast.AST) -> Optional[str]:
    """Return 'seq' (list/tuple -> .index danger) or 'map' (dict -> subscript
    danger) when the literal's string members are majority risk tokens."""
    elems = _str_elems(node)
    if len(elems) < 2:
        return None
    upper = [e.upper() for e in elems]
    hits = sum(1 for e in upper if e in RISK_TOKENS)
    if hits < 2 or hits * 2 < len(upper):  # need majority AND >=2
        return None
    if isinstance(node, ast.Dict):
        return "map"
    if isinstance(node, (ast.List, ast.Tuple)):
        return "seq"
    return None


def _vocab_names(tree: ast.AST) -> Dict[str, str]:
    """{bound_name: 'seq'|'map'} for every assignment of a risk-vocab literal."""
    out: Dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            kind = _is_risk_vocab(n.value)
            if not kind:
                continue
            for tgt in n.targets:
                if isinstance(tgt, ast.Name):
                    out[tgt.id] = kind
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            kind = _is_risk_vocab(n.value)
            if kind and isinstance(n.target, ast.Name):
                out[n.target.id] = kind
    return out


def _non_literal(node: ast.AST) -> bool:
    return not (isinstance(node, ast.Constant))


def _enclosing_funcs(tree: ast.AST) -> List[ast.AST]:
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _func_of(site: ast.AST, funcs: List[ast.AST]) -> Optional[ast.AST]:
    best = None
    for f in funcs:
        for sub in ast.walk(f):
            if sub is site:
                best = f  # innermost wins because walk of nested funcs reaches it too
    return best


def _has_membership_guard(scope: ast.AST, vocab_name: str) -> bool:
    """True if `scope` contains an `if <x> in <vocab>` / `<x> not in <vocab>` test."""
    for n in ast.walk(scope):
        if isinstance(n, ast.Compare):
            for op, comp in zip(n.ops, n.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and isinstance(comp, ast.Name) \
                        and comp.id == vocab_name:
                    return True
    return False


def _tries_catching(tree: ast.AST) -> List[Tuple[int, int]]:
    """(start_lineno, end_lineno) spans of Try blocks whose handlers catch a safe exc."""
    spans: List[Tuple[int, int]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Try):
            safe = False
            for h in n.handlers:
                if h.type is None:
                    safe = True
                elif isinstance(h.type, ast.Name) and h.type.id in SAFE_EXC:
                    safe = True
                elif isinstance(h.type, ast.Tuple):
                    if any(isinstance(e, ast.Name) and e.id in SAFE_EXC for e in h.type.elts):
                        safe = True
            if safe:
                body_nodes = n.body
                lo = min((b.lineno for b in body_nodes), default=n.lineno)
                hi = max((getattr(b, "end_lineno", b.lineno) for b in body_nodes), default=n.lineno)
                spans.append((lo, hi))
    return spans


def _in_safe_try(lineno: int, spans: List[Tuple[int, int]]) -> bool:
    return any(lo <= lineno <= hi for lo, hi in spans)


def _main_guard_spans(tree: ast.AST) -> List[Tuple[int, int]]:
    """Line spans of `if __name__ == "__main__":` blocks -- in-file self-tests,
    not the production verdict path, so their index/subscript sites are exempt
    (they run on author-controlled fixture data, never a live degenerate label)."""
    spans: List[Tuple[int, int]] = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.If) or not isinstance(n.test, ast.Compare):
            continue
        t = n.test
        names = {getattr(x, "id", None) for x in [t.left, *t.comparators] if isinstance(x, ast.Name)}
        consts = {x.value for x in [t.left, *t.comparators] if isinstance(x, ast.Constant)}
        if "__name__" in names and "__main__" in consts:
            body = n.body
            lo = min((b.lineno for b in body), default=n.lineno)
            hi = max((getattr(b, "end_lineno", b.lineno) for b in body), default=n.lineno)
            spans.append((lo, hi))
    return spans


def _is_selftest_name(name: str) -> bool:
    """Conservative in-file self-test function names (zo-sentinel convention:
    __main__ calls a `_run_self_test`/`_selftest` helper). Narrow by design --
    a production verdict function is never named this way -- so exempting it
    cannot hide a real consumer bypass (scoped heuristic, mirrors the tests/ path
    exemption for test helpers that live outside a tests/ dir)."""
    n = (name or "").lower()
    return (n in {"_run_self_test", "_selftest", "_self_test", "run_self_test", "selftest"}
            or n.startswith("test_") or n.endswith("_selftest") or n.endswith("_self_test"))


def _selftest_fn_spans(tree: ast.AST) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_selftest_name(n.name):
            spans.append((n.lineno, getattr(n, "end_lineno", n.lineno)))
    return spans


def audit_source(src: str, rel: str) -> Dict:
    """Return {'sites': [...], 'unguarded': int} or {'parse_error': str}."""
    try:
        tree = ast.parse(src, filename=rel)
    except SyntaxError as e:
        return {"parse_error": f"{type(e).__name__}: {e}"}

    vocab = _vocab_names(tree)
    if not vocab:
        return {"sites": [], "unguarded": 0}

    funcs = _enclosing_funcs(tree)
    try_spans = _tries_catching(tree)
    main_spans = _main_guard_spans(tree) + _selftest_fn_spans(tree)

    # A vocab-map subscript that READS KeyErrors on a missing key. A plain
    # `MAP[x] = v` (Assign target) is a pure write -> safe; but `MAP[x] += 1`
    # (AugAssign target) and `del MAP[x]` (Del) both read first -> dangerous.
    # Collect the dangerous Store/Del subscript node ids so the walk can tell
    # them apart from safe plain-assignment writes.
    read_subscripts: Set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Subscript):
            read_subscripts.add(id(n.target))
        elif isinstance(n, ast.Delete):
            for t in n.targets:
                if isinstance(t, ast.Subscript):
                    read_subscripts.add(id(t))

    sites: List[Dict] = []

    for n in ast.walk(tree):
        vocab_name = None
        kind = None
        xnode = None
        # ORDER.index(x)  /  MAP.pop(x)  (1-arg pop has no default -> KeyErrors)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and isinstance(n.func.value, ast.Name) and n.args and _non_literal(n.args[0]):
            vn = n.func.value.id
            if n.func.attr == "index" and vocab.get(vn) == "seq":
                vocab_name, kind, xnode = vn, "index", n.args[0]
            elif n.func.attr == "pop" and vocab.get(vn) == "map" and len(n.args) == 1:
                vocab_name, kind, xnode = vn, "pop", n.args[0]
        # MAP[x]  -- Load, or a read-modify Store (AugAssign target) / Del
        elif isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) \
                and vocab.get(n.value.id) == "map" \
                and (isinstance(n.ctx, ast.Load) or id(n) in read_subscripts):
            key = n.slice.value if isinstance(n.slice, ast.Index) else n.slice  # py<3.9 compat
            if _non_literal(key):
                k = "subscript" if isinstance(n.ctx, ast.Load) else "augsubscript"
                vocab_name, kind, xnode = n.value.id, k, key

        if vocab_name is None:
            continue

        lineno = getattr(n, "lineno", 0)
        if _in_safe_try(lineno, main_spans):   # inside an `if __name__ == "__main__"` self-test
            continue
        guarded = _in_safe_try(lineno, try_spans)
        if not guarded:
            fn = _func_of(n, funcs) or tree
            guarded = _has_membership_guard(fn, vocab_name)
        sites.append({
            "line": lineno, "vocab": vocab_name, "kind": kind, "guarded": bool(guarded),
        })

    unguarded = sum(1 for s in sites if not s["guarded"])
    return {"sites": sites, "unguarded": unguarded}


# ----------------------------------------------------------------------------- scan

def scan(root: str) -> Dict:
    files: Dict[str, Dict] = {}
    parse_errors: Dict[str, str] = {}
    for rel in _tracked_py(root):
        if _is_exempt(rel):
            continue
        path = os.path.join(root, rel)
        try:
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()
        except (OSError, UnicodeDecodeError) as e:
            parse_errors[rel] = f"read: {e}"
            continue
        res = audit_source(src, rel)
        if "parse_error" in res:
            parse_errors[rel] = res["parse_error"]
            continue
        if res["sites"]:
            files[rel] = res
    return {"files": files, "parse_errors": parse_errors}


def _unguarded_map(scan_res: Dict) -> Dict[str, int]:
    return {rel: r["unguarded"] for rel, r in scan_res["files"].items() if r["unguarded"] > 0}


# ----------------------------------------------------------------------------- baseline

def _load_baseline() -> Dict[str, int]:
    if not os.path.exists(BASELINE):
        return {}
    with open(BASELINE, "r", encoding="utf-8") as f:
        return dict(json.load(f).get("files", {}))


def _write_baseline(unguarded: Dict[str, int]) -> None:
    with open(BASELINE, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "files": dict(sorted(unguarded.items()))}, f, indent=2)
        f.write("\n")


# ----------------------------------------------------------------------------- cli

def _cmd_report(root: str) -> int:
    res = scan(root)
    for rel in sorted(res["files"]):
        r = res["files"][rel]
        g = sum(1 for s in r["sites"] if s["guarded"])
        print(f"{rel}: {len(r['sites'])} risk-vocab index/subscript site(s) "
              f"[{g} guarded / {r['unguarded']} UNGUARDED]")
        for s in r["sites"]:
            tag = "GUARDED " if s["guarded"] else "UNGUARD "
            print(f"    L{s['line']:>4} {tag} {s['vocab']}.{s['kind']}")
    if res["parse_errors"]:
        print(f"\n[parse_errors] {len(res['parse_errors'])} tracked file(s) unparseable (UNKNOWN):")
        for rel, err in sorted(res["parse_errors"].items()):
            print(f"    {rel}: {err}")
    ung = _unguarded_map(res)
    print(f"\n[summary] {len(res['files'])} files with risk-vocab sites, "
          f"{len(ung)} with UNGUARDED sites ({sum(ung.values())} total unguarded).")
    return 0


def _cmd_check(root: str) -> int:
    res = scan(root)
    if res["parse_errors"]:
        print(f"UNKNOWN: {len(res['parse_errors'])} tracked file(s) failed to parse "
              f"(cannot certify Control-(a); treat as not-clean):", file=sys.stderr)
        for rel, err in sorted(res["parse_errors"].items()):
            print(f"    {rel}: {err}", file=sys.stderr)
        return 2
    cur = _unguarded_map(res)
    base = _load_baseline()
    offenders: List[str] = []
    for rel, n in sorted(cur.items()):
        b = base.get(rel, 0)
        if n > b:
            offenders.append(f"{rel}: unguarded {b} -> {n} "
                             f"({'NEW file' if rel not in base else 'INCREASED'})")
    if offenders:
        print("RED: new/increased UNGUARDED risk-vocab index/subscript "
              "(Control-(a) bypass risk):", file=sys.stderr)
        for o in offenders:
            print(f"    {o}", file=sys.stderr)
        print("Guard with `if x in VOCAB` or try/except (ValueError|KeyError), "
              "or add `--repin` once intentionally cured.", file=sys.stderr)
        return 1
    tightened = sorted(r for r in base if base[r] > cur.get(r, 0))
    if tightened:
        print(f"OK (and {len(tightened)} file(s) tightened below baseline; "
              f"run --repin to bank the slack): {', '.join(tightened)}")
    print(f"OK: no new/increased unguarded sites vs baseline "
          f"({len(base)} baselined, {sum(cur.values())} current unguarded).")
    return 0


def _cmd_repin(root: str) -> int:
    res = scan(root)
    if res["parse_errors"]:
        print("refusing to repin while tracked files fail to parse (UNKNOWN).", file=sys.stderr)
        return 2
    cur = _unguarded_map(res)
    _write_baseline(cur)
    print(f"repinned baseline: {len(cur)} file(s), {sum(cur.values())} unguarded sites -> {BASELINE}")
    return 0


# ----------------------------------------------------------------------------- self-test (two-pole)

_GUARDED_FIX = '''
SEV = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
def rank(lbl):
    try:
        return SEV.index(lbl.upper())
    except ValueError:
        return len(SEV)
'''
_GUARDED_IN = '''
RISK_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
def rank(lbl):
    if lbl in RISK_ORDER:
        return RISK_ORDER.index(lbl)
    return -1
'''
_BYPASS_INDEX = '''
SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
def rank(lbl):
    return SEVERITY_ORDER.index(lbl.upper())
'''
_BYPASS_MAP = '''
LABEL_TO_TIER = {"LOW": "TRUSTED_GENERAL", "HIGH": "CAUTION_LIMITED", "CRITICAL": "HIGH_RISK_ISOLATED"}
def tier(lbl):
    return LABEL_TO_TIER[lbl.upper()]
'''
_NOT_RISK = '''
WEEKDAYS = ["MON", "TUE", "WED"]
def idx(d):
    return WEEKDAYS.index(d)
'''
_GET_SAFE = '''
LABEL_TO_TIER = {"LOW": "ok", "HIGH": "bad", "CRITICAL": "worst"}
def tier(lbl):
    return LABEL_TO_TIER.get(lbl.upper(), "UNKNOWN")
'''
_MAIN_SELFTEST = '''
SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
if __name__ == "__main__":
    lbl = "HIGH"
    assert SEVERITY_ORDER.index(lbl) == 1
'''
_FN_SELFTEST = '''
SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
def _run_self_test():
    lbl = "HIGH"
    assert SEVERITY_ORDER.index(lbl) == 1
'''
# AugAssign target subscript reads-then-writes -> KeyErrors like a Load (the FN
# the frontier adversary caught). `MAP[x] = v` is a pure write -> safe.
_BYPASS_AUGASSIGN = '''
TIER_COUNTS = {"LOW": 0, "HIGH": 0, "CRITICAL": 0}
def count(tier):
    TIER_COUNTS[tier] += 1
'''
_SAFE_STORE = '''
TIER_COUNTS = {"LOW": 0, "HIGH": 0, "CRITICAL": 0}
def put(tier, v):
    TIER_COUNTS[tier] = v
'''
_BYPASS_DEL = '''
LABEL_TO_TIER = {"LOW": "a", "HIGH": "b", "CRITICAL": "c"}
def drop(lbl):
    del LABEL_TO_TIER[lbl]
'''
_BYPASS_POP = '''
LABEL_TO_TIER = {"LOW": "a", "HIGH": "b", "CRITICAL": "c"}
def take(lbl):
    return LABEL_TO_TIER.pop(lbl)
'''
_SAFE_POP_DEFAULT = '''
LABEL_TO_TIER = {"LOW": "a", "HIGH": "b", "CRITICAL": "c"}
def take(lbl):
    return LABEL_TO_TIER.pop(lbl, None)
'''


def _selftest() -> int:
    cases = [
        ("guarded-try", _GUARDED_FIX, 1, 0),
        ("guarded-in", _GUARDED_IN, 1, 0),
        ("bypass-index", _BYPASS_INDEX, 1, 1),
        ("bypass-map", _BYPASS_MAP, 1, 1),
        ("not-risk-vocab", _NOT_RISK, 0, 0),   # WEEKDAYS not risk tokens -> no site
        ("dict-get-safe", _GET_SAFE, 0, 0),    # .get is not a subscript -> no danger site
        ("main-selftest-exempt", _MAIN_SELFTEST, 0, 0),  # site inside __main__ -> exempt
        ("fn-selftest-exempt", _FN_SELFTEST, 0, 0),      # site inside _run_self_test -> exempt
        ("bypass-augassign", _BYPASS_AUGASSIGN, 1, 1),   # MAP[x] += 1 reads -> danger
        ("safe-plain-store", _SAFE_STORE, 0, 0),         # MAP[x] = v write-only -> safe
        ("bypass-del", _BYPASS_DEL, 1, 1),               # del MAP[x] -> danger
        ("bypass-pop", _BYPASS_POP, 1, 1),               # MAP.pop(x) 1-arg -> danger
        ("safe-pop-default", _SAFE_POP_DEFAULT, 0, 0),   # MAP.pop(x, None) -> safe
    ]
    ok = True
    for name, src, want_sites, want_ung in cases:
        r = audit_source(src, name + ".py")
        if "parse_error" in r:
            print(f"FAIL {name}: parse_error {r['parse_error']}"); ok = False; continue
        got_sites, got_ung = len(r["sites"]), r["unguarded"]
        status = "ok" if (got_sites == want_sites and got_ung == want_ung) else "FAIL"
        if status == "FAIL":
            ok = False
        print(f"  [{status}] {name}: sites={got_sites}(want {want_sites}) "
              f"unguarded={got_ung}(want {want_ung})")
    # parse-error is UNKNOWN, not clean
    pe = audit_source("def f(:\n  pass", "broken.py")
    if "parse_error" not in pe:
        print("  [FAIL] broken source should report parse_error"); ok = False
    else:
        print("  [ok] broken source -> parse_error (UNKNOWN)")
    print("PASS" if ok else "SELFTEST-FAILED")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="ratchet vs baseline (rc=1 new/increased, rc=2 unknown)")
    ap.add_argument("--repin", action="store_true", help="rewrite the baseline from the current tree")
    ap.add_argument("--self-test", action="store_true", help="two-pole fixture self-test (no repo needed)")
    ap.add_argument("--root", default=None, help="repo root (default: git toplevel of this file)")
    args = ap.parse_args(argv)

    if args.self_test:
        return _selftest()
    root = args.root or _repo_root(HERE)
    if args.repin:
        return _cmd_repin(root)
    if args.check:
        return _cmd_check(root)
    return _cmd_report(root)


if __name__ == "__main__":
    raise SystemExit(main())
