#!/usr/bin/env python3
"""fly_bus_plane_check.py -- GR-29: shipped Fly code may not address the :8772 bus.

THE GAP (LOCO_CHAIRMAN.md 6.1, GR-29, class GC-11 plane confusion)
  The factory write_service (DuckDB) listens on 127.0.0.1:8772 on the ZoComputer host.
  It is NOT in the Fly image. A module the Dockerfile copies into the Fly image that
  POSTs to :8772 in a request path can only fail there (Errno 111 -> 500). AP-006 checks
  table names inside :8772 SQL; nothing checked whether shipped code may address :8772
  at all.

WHAT COUNTS (static, AST; imports nothing, runs nothing)
  A hit is a string constant containing "8772" that is CODE in a file the Dockerfile
  COPYs, outside tools/ (host-side scripts legitimately address the bus):
    * docstrings are not code (module / class / function first-statement strings)
    * comments are not tokens at all, so never seen
    * anything under `if __name__ == "__main__":` is not a request path
  Python only; the scan walks every COPY source token (file or directory).

  rc 0 GREEN (no hits), 1 RED (hits listed), 2 UNKNOWN (no Dockerfile / no COPY / a
  shipped file that does not parse -- never read as green).

  python tools/fly_bus_plane_check.py [--root .] [--json]
  python tools/fly_bus_plane_check.py --self-test
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from image_ship_check import copy_source_tokens  # noqa: E402

NEEDLE = "8772"
EXCLUDE_PREFIXES = ("tools/",)
GREEN, RED, UNKNOWN = 0, 1, 2


class Unknown(Exception):
    pass


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(n, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _main_guard_nodes(tree: ast.Module) -> set[int]:
    ids = set()
    for n in tree.body:
        if isinstance(n, ast.If) and isinstance(n.test, ast.Compare) \
                and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__" \
                and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in n.test.comparators):
            for sub in ast.walk(n):
                ids.add(id(sub))
    return ids


def scan_source(src: str, name: str = "<src>") -> list[int]:
    """Line numbers of code string constants that contain :8772."""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        raise Unknown(f"{name} does not parse: {e}")
    skip = _docstring_nodes(tree) | _main_guard_nodes(tree)
    lines = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and NEEDLE in n.value \
                and id(n) not in skip:
            lines.append(n.lineno)
    return sorted(set(lines))


def shipped_py_files(root: str) -> list[str]:
    df = os.path.join(root, "Dockerfile")
    try:
        text = open(df, encoding="utf-8").read()
    except OSError:
        raise Unknown("Dockerfile missing")
    tokens = list(copy_source_tokens(text))
    if not tokens:
        raise Unknown("Dockerfile has no COPY sources")
    out = set()
    for tok in tokens:
        tok = tok.rstrip("/")
        p = os.path.join(root, tok)
        if os.path.isfile(p) and p.endswith(".py"):
            out.add(os.path.relpath(p, root).replace(os.sep, "/"))
        elif os.path.isdir(p):
            for dp, dn, fns in os.walk(p):
                dn[:] = [d for d in dn if d != "__pycache__"]
                for fn in fns:
                    if fn.endswith(".py"):
                        out.add(os.path.relpath(os.path.join(dp, fn), root).replace(os.sep, "/"))
    return sorted(f for f in out if not f.startswith(EXCLUDE_PREFIXES))


def run(root: str) -> tuple[int, dict]:
    try:
        files = shipped_py_files(root)
        hits = []
        for rel in files:
            src = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()
            if NEEDLE not in src:
                continue
            for ln in scan_source(src, rel):
                hits.append(f"{rel}:{ln}")
    except Unknown as e:
        return UNKNOWN, {"verdict": "UNKNOWN", "reason": str(e)}
    return (RED if hits else GREEN), {"verdict": "RED" if hits else "GREEN",
                                      "shipped_py": len(files), "hits": hits}


def self_test() -> int:
    ok = True

    def expect(name, cond):
        nonlocal ok
        print(("pass " if cond else "FAIL ") + name)
        ok = ok and cond

    expect("code URL is a hit", scan_source('U = "http://127.0.0.1:8772/query"\n') == [1])
    expect("f-string URL is a hit", scan_source('h = 1\nU = f"http://127.0.0.1:8772/{h}"\n') == [2])
    expect("module docstring is not a hit", scan_source('"""posts to :8772"""\nx = 1\n') == [])
    expect("function docstring is not a hit",
           scan_source('def f():\n    """was :8772"""\n    return 1\n') == [])
    expect("comment is not a hit", scan_source('x = 1  # was :8772\n') == [])
    expect("__main__ block is not a hit",
           scan_source('x = 1\nif __name__ == "__main__":\n    u = "http://127.0.0.1:8772"\n') == [])
    try:
        scan_source("def (:\n")
        expect("unparseable is UNKNOWN", False)
    except Unknown:
        expect("unparseable is UNKNOWN", True)
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "tools"))
        open(os.path.join(d, "Dockerfile"), "w").write("FROM x\nCOPY a.py b.py /srv/\nCOPY tools /srv/tools\n")
        open(os.path.join(d, "a.py"), "w").write('U = "http://127.0.0.1:8772/q"\n')
        open(os.path.join(d, "b.py"), "w").write('"""no bus"""\n')
        open(os.path.join(d, "tools", "t.py"), "w").write('U = "http://127.0.0.1:8772/q"\n')
        open(os.path.join(d, "unshipped.py"), "w").write('U = "http://127.0.0.1:8772/q"\n')
        rc, out = run(d)
        expect("end-to-end: only the shipped non-tools file is red",
               rc == RED and out["hits"] == ["a.py:1"])
        open(os.path.join(d, "a.py"), "w").write('U = "postgres"\n')
        rc, _ = run(d)
        expect("end-to-end green after the fix", rc == GREEN)
        os.remove(os.path.join(d, "Dockerfile"))
        rc, _ = run(d)
        expect("no Dockerfile is UNKNOWN", rc == UNKNOWN)
    print("SELF-TEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    rc, out = run(a.root)
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"fly_bus_plane_check: {out['verdict']}"
              + (f" ({out['shipped_py']} shipped .py, {len(out['hits'])} hit(s))" if "hits" in out
                 else f": {out.get('reason')}"))
        for h in out.get("hits", []):
            print("  " + h)
    return rc


if __name__ == "__main__":
    sys.exit(main())
