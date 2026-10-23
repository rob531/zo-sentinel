#!/usr/bin/env python3
"""Census the ONE shape that floods a result: "N failures: <all N of them>".

friction family `line-count-bound-on-a-oneline-payload`.  A bound by LINE COUNT
does not bound a one-line payload: `head -25`, `tail -40` and
`Select-Object -First N` all pass a single 10KB line through whole.  So a
consumer CANNOT defend itself, and the only place the bound can live is the
producer.  This finds the producers.

A print()/write() is IN SHAPE iff, in the same call, it both
  (1) renders `len(X)` -- so the author already knows the length is not fixed --
  (2) renders the WHOLE of something rooted at that same X, either as a
      `sep.join(... for ... in X)` or as a bare %-interpolation of X (whose repr
      is one line),
  (3) and nothing in the call applies a slice bound.

The cure is `tools/bounded.py`:

    print("STRICT: %d bad: %s" % (len(bad), sample(bad, where="artifacts/x.json")))

EXIT CODES -- no outcome collapsed into another
    0  no in-shape call site in the scanned subtrees
    1  at least one in-shape call site (each is printed)
    2  REFUSED / could not scan (no subtree existed). Unknown is not zero (R6).

UNPARSED files are counted and NAMED in the basis, never silently skipped.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBTREES_DEFAULT = ("tools", "app", "scripts", "mcp_servers")
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache"}


def root_name(node):
    """The innermost Name/Attribute/Subscript root, as a comparable string."""
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            node = node.value
            continue
        if isinstance(node, ast.Subscript):
            base = root_name(node.value)
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                return "%s[%s]" % (base, node.slice.value)
            return base
        if isinstance(node, ast.Call):
            node = node.func
            continue
        return None


def len_roots(call):
    out = set()
    for n in ast.walk(call):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id == "len" and n.args:
            r = root_name(n.args[0])
            if r:
                out.add(r)
    return out


def rendered_roots(call):
    """Roots whose WHOLE content reaches the output."""
    out = set()
    for n in ast.walk(call):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "join" and n.args:
            arg = n.args[0]
            if isinstance(arg, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
                for gen in arg.generators:
                    r = root_name(gen.iter)
                    if r:
                        out.add(r)
            else:
                r = root_name(arg)
                if r:
                    out.add(r)
    for n in ast.walk(call):
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod):
            right = n.right
            elts = right.elts if isinstance(right, ast.Tuple) else [right]
            for e in elts:
                if isinstance(e, (ast.Name, ast.Attribute, ast.Subscript)):
                    r = root_name(e)
                    if r:
                        out.add(r)
    return out


def sliced(call):
    for n in ast.walk(call):
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice):
            return True
    return False


def scan_source(src, rel):
    """Yield (rel, lineno, shared_roots) for every in-shape call in `src`."""
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        fname = n.func.id if isinstance(n.func, ast.Name) else (
            n.func.attr if isinstance(n.func, ast.Attribute) else None)
        if fname not in ("print", "write"):
            continue
        if sliced(n):
            continue
        shared = len_roots(n) & rendered_roots(n)
        if shared:
            yield (rel, n.lineno, sorted(shared))


def census(root=ROOT_DEFAULT, subtrees=SUBTREES_DEFAULT):
    hits, unparsed, scanned, present = [], [], 0, []
    for sub in subtrees:
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            continue
        present.append(sub)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in sorted(filenames):
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, root).replace("\\", "/")
                scanned += 1
                try:
                    with open(p, encoding="utf-8") as fh:
                        src = fh.read()
                    hits.extend(scan_source(src, rel))
                except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                    unparsed.append((rel, "%s: %s" % (type(exc).__name__, exc)))
    return {"hits": sorted(hits), "unparsed": unparsed,
            "scanned": scanned, "subtrees": present, "root": root}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--subtrees", default=",".join(SUBTREES_DEFAULT))
    args = ap.parse_args(argv)

    r = census(args.root, tuple(s for s in args.subtrees.split(",") if s))
    if not r["subtrees"]:
        print("REFUSED: none of the requested subtrees exist under %s -- "
              "this is UNKNOWN, not zero (R6)" % args.root, file=sys.stderr)
        return 2

    print("CENSUS family=line-count-bound-on-a-oneline-payload "
          "shape=count-plus-whole-list-on-one-line")
    print("  BASIS: %d .py file(s) under %s of %s; UNPARSED=%d (unknown, not zero)"
          % (r["scanned"], ",".join(r["subtrees"]), r["root"], len(r["unparsed"])))
    for rel, exc in r["unparsed"]:
        print("    UNPARSED %s -- %s" % (rel, exc[:160]))
    print("  IN-SHAPE call sites: %d   (cure: tools/bounded.py sample())" % len(r["hits"]))
    for rel, ln, roots in r["hits"]:
        print("    %s:%d  len() and the whole list share %s" % (rel, ln, ",".join(roots)))
    return 1 if r["hits"] else 0


if __name__ == "__main__":
    sys.exit(main())
