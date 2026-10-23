#!/usr/bin/env python3
"""Add the one import a staged module uses but never declares.

THE FAMILY.  A scaffolded staged service uses a name -- ``Depends``, ``Session``,
``func``, ``StaticPool`` -- that has exactly ONE provenance anywhere in this
codebase, and never imports it.  The module then dies at import time with
``NameError: name 'X' is not defined`` and ``promote_staged_to_active.py``
refuses to promote it.  Measured 2026-10-02 on tracked ``services/staged``:
21 sites in 20 files, one of them ``risk_tier_transition_alert``, named in
chairman issue #4002.

WHY A TABLE, AND WHY THE TABLE IS CHECKED AGAINST THE REPO.  The hollow gate
(FU-042) passed 324 of 371 orphans because it matched things that *resembled*
the exemplar.  So this tool guesses nothing.  Every name it will touch is
declared below AND confirmed against the tree: the repo must import that name
from that module and from no other.  A name with two provenances, or none, is
reported UNFIXABLE and left byte-identical.  That refusal is the discriminating
power, and ``--self-test`` observes it going red on purpose.

IDEMPOTENT BY CONSTRUCTION.  The work list is re-derived from ruff F821 on
every run, so once the import exists the finding is gone and a second run is a
no-op.  Nothing is keyed on a marker comment, a ledger, or a dated file.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# name -> the single module this codebase imports it from.  ASSERTED here,
# CONFIRMED against the tree by confirm_provenance() before any edit.
PROVENANCE = {
    "Depends": "fastapi",
    "FastAPI": "fastapi",
    "HTTPException": "fastapi",
    "APIRouter": "fastapi",
    "Query": "fastapi",
    "Session": "sqlalchemy.orm",
    "StaticPool": "sqlalchemy.pool",
    "TestClient": "fastapi.testclient",
    "create_engine": "sqlalchemy",
    "func": "sqlalchemy",
    "or_": "sqlalchemy",
    "and_": "sqlalchemy",
    "timedelta": "datetime",
    "timezone": "datetime",
}

STAGED_GLOB = "services/staged/**/*.py"
UNDEF_RE = re.compile(r"Undefined name `([^`]+)`")


def die(msg: str, rc: int = 2) -> None:
    print("REFUSED: " + msg, file=sys.stderr)
    raise SystemExit(rc)


def tracked_staged(root: Path) -> list[str]:
    p = subprocess.run(["git", "ls-files", STAGED_GLOB], cwd=root,
                       capture_output=True, text=True)
    if p.returncode != 0:
        die("git ls-files failed in %s: %s" % (root, p.stderr.strip()))
    return [l for l in p.stdout.splitlines() if l.strip()]


def ruff_f821(root: Path, rel_paths: list[str]) -> list[tuple[str, int, str]]:
    """[(relpath, lineno, name)] -- every F821 'Undefined name' ruff reports.

    A missing ruff is rc=2, never an empty list: unknown is not zero (R6).
    """
    if not rel_paths:
        return []
    if shutil.which("ruff") is None:
        die("ruff is not on PATH; F821 cannot be derived. Unknown is not zero.")
    out: list[tuple[str, int, str]] = []
    for i in range(0, len(rel_paths), 400):          # keep argv under the OS cap
        chunk = rel_paths[i:i + 400]
        p = subprocess.run(
            ["ruff", "check", "--select", "F821", "--output-format", "json",
             "--no-cache"] + chunk,
            cwd=root, capture_output=True, text=True)
        if not p.stdout.strip():
            if p.returncode not in (0, 1):
                die("ruff exited %d with no JSON: %s" % (p.returncode, p.stderr.strip()[:300]))
            continue
        try:
            rows = json.loads(p.stdout)
        except json.JSONDecodeError:
            die("ruff did not return JSON: %s" % p.stdout[:300])
        for r in rows:
            m = UNDEF_RE.search(r.get("message", ""))
            if not m:
                continue
            rel = str(Path(r["filename"]).resolve().relative_to(root.resolve())).replace("\\", "/")
            out.append((rel, int((r.get("location") or {}).get("row") or 0), m.group(1)))
    return out


def same_object(mod_a: str, mod_b: str, name: str) -> bool:
    """True when `name` is literally the SAME object in both modules.

    This is how a re-export is distinguished from a collision without guessing:
    ``sqlalchemy.StaticPool is sqlalchemy.pool.StaticPool`` (one object, two
    paths) but ``requests.Session is not sqlalchemy.orm.Session`` (two objects,
    same spelling).  A module that will not import is a FAILURE, not a pass --
    unknown is not zero (R6).
    """
    code = ("import importlib,sys\n"
            "a=importlib.import_module(sys.argv[1]);b=importlib.import_module(sys.argv[2])\n"
            "n=sys.argv[3]\n"
            "sys.exit(0 if getattr(a,n,object()) is getattr(b,n,object()) else 1)\n")
    p = subprocess.run([sys.executable, "-c", code, mod_a, mod_b, name],
                       capture_output=True, text=True)
    return p.returncode == 0


def confirm_provenance(root: Path, name: str, module: str) -> tuple[bool, set[str]]:
    """True when every module the tree imports `name` from yields the SAME object."""
    p = subprocess.run(["git", "grep", "-h", "-E",
                        r"^\s*from\s+[A-Za-z_][A-Za-z0-9_.]*\s+import\b.*\b%s\b" % re.escape(name),
                        "--", "*.py"], cwd=root, capture_output=True, text=True)
    mods: set[str] = set()
    for line in p.stdout.splitlines():
        m = re.match(r"\s*from\s+([A-Za-z_][A-Za-z0-9_.]*)\s+import\s+(.*)$", line)
        if not m:
            continue
        names = {n.strip().split(" as ")[0].strip(" ()")
                 for n in m.group(2).replace("(", "").replace(")", "").split(",")}
        if name in names:
            mods.add(m.group(1))
    if module not in mods:
        return False, mods
    extra = mods - {module}
    return all(same_object(module, other, name) for other in extra), mods


def plan_edit(src: str, name: str, module: str) -> tuple[str, str] | None:
    """(new_src, how) or None when the name is already imported at top level."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    lines = src.splitlines(keepends=True)
    last_import_end = None
    single_line_from = None          # (lineno, end_lineno) of `from <module> import a, b`
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import_end = max(last_import_end or 0, node.end_lineno or node.lineno)
        if isinstance(node, ast.ImportFrom) and node.module == module and not node.level:
            for a in node.names:
                if a.name == name or a.name == "*":
                    return None      # already there -- idempotence, not a special case
            if (node.end_lineno or node.lineno) == node.lineno and "(" not in lines[node.lineno - 1]:
                single_line_from = node.lineno

    if single_line_from is not None:
        i = single_line_from - 1
        stripped = lines[i].rstrip("\r\n")
        eol = lines[i][len(stripped):]
        return "".join(lines[:i] + [stripped + ", " + name + eol] + lines[i + 1:]), \
               "extended line %d (from %s import ...)" % (single_line_from, module)

    insert_at = last_import_end if last_import_end else 0
    if not last_import_end:                      # after a module docstring, if any
        if tree.body and isinstance(tree.body[0], ast.Expr) and \
                isinstance(getattr(tree.body[0], "value", None), ast.Constant) and \
                isinstance(tree.body[0].value.value, str):
            insert_at = tree.body[0].end_lineno
    new_line = "from %s import %s\n" % (module, name)
    return "".join(lines[:insert_at] + [new_line] + lines[insert_at:]), \
           "inserted new import at line %d" % (insert_at + 1)


def collect(root: Path, rel_paths: list[str]):
    """(fixable, unfixable) from the live F821 set."""
    findings = ruff_f821(root, rel_paths)
    fixable, unfixable = [], []
    confirmed: dict[str, bool] = {}
    for rel, lineno, name in findings:
        module = PROVENANCE.get(name)
        if module is None:
            unfixable.append((rel, lineno, name, "no declared provenance"))
            continue
        if name not in confirmed:
            ok, mods = confirm_provenance(root, name, module)
            confirmed[name] = ok
            if not ok:
                confirmed[name + "__why"] = (
                    "tree imports it from %s; not all of those are the same object as "
                    "%s.%s" % (sorted(mods) or ["nowhere"], module, name))
        if not confirmed[name]:
            unfixable.append((rel, lineno, name, confirmed[name + "__why"]))
            continue
        fixable.append((rel, lineno, name, module))
    return fixable, unfixable


def apply_fixes(root: Path, fixable) -> list[tuple[str, str, str]]:
    done = []
    for rel, _lineno, name, module in fixable:
        path = root / rel
        src = path.read_text(encoding="utf-8")
        planned = plan_edit(src, name, module)
        if planned is None:
            continue
        new_src, how = planned
        try:
            compile(new_src, str(path), "exec")
        except SyntaxError as e:
            print("  SKIP %s: edit would not compile (%s)" % (rel, e))
            continue
        path.write_text(new_src, encoding="utf-8", newline="")
        done.append((rel, name, how))
    return done


def self_test() -> int:
    """The negative control.  Three poles, each OBSERVED, or rc=1."""
    poles = []
    # POLE 0 -- the identity oracle discriminates a re-export from a collision.
    poles.append(("POLE 0  sqlalchemy.StaticPool IS sqlalchemy.pool.StaticPool (re-export)",
                  same_object("sqlalchemy.pool", "sqlalchemy", "StaticPool")))
    poles.append(("POLE 0  requests.Session is NOT sqlalchemy.orm.Session (collision, RED)",
                  not same_object("sqlalchemy.orm", "requests", "Session")))
    poles.append(("POLE 0  an unimportable module is REFUSED, never passed",
                  not same_object("fastapi.testclient", "starless.testclient", "TestClient")))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        (root / "services" / "staged" / "fixture_known").mkdir(parents=True)
        (root / "services" / "staged" / "fixture_unknown").mkdir(parents=True)
        known = root / "services" / "staged" / "fixture_known" / "router.py"
        unknown = root / "services" / "staged" / "fixture_unknown" / "router.py"
        known.write_text(
            "from fastapi import APIRouter\n\n\n"
            "def handler(db=Depends(lambda: None)):\n    return db\n", encoding="utf-8")
        unknown.write_text(
            "from fastapi import APIRouter\n\n\n"
            "def handler(db=Frobnicate()):\n    return db\n", encoding="utf-8")
        # provenance must be confirmable inside the fixture tree, so give it a witness
        (root / "app.py").write_text("from fastapi import Depends\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        before_unknown = unknown.read_bytes()

        rels = tracked_staged(root)
        fixable, unfixable = collect(root, rels)

        # POLE 1 -- the known name is seen RED and then fixed.
        red = [f for f in fixable if f[2] == "Depends"]
        poles.append(("POLE 1  Depends observed F821-RED before the fix", bool(red)))
        apply_fixes(root, fixable)
        p = subprocess.run([sys.executable, "-c",
                            "import ast,sys;src=open(sys.argv[1]).read();"
                            "exec(compile(src,'f','exec'),{})", str(known)],
                           capture_output=True, text=True)
        poles.append(("POLE 1  fixture_known now executes (rc=%d)" % p.returncode,
                      p.returncode == 0))

        # POLE 2 -- the unknown name is REFUSED and the file is untouched.
        poles.append(("POLE 2  Frobnicate reported UNFIXABLE",
                      any(u[2] == "Frobnicate" for u in unfixable)))
        poles.append(("POLE 2  fixture_unknown byte-identical",
                      unknown.read_bytes() == before_unknown))

        # POLE 3 -- idempotence: a second run has nothing to do.
        again, _ = collect(root, rels)
        poles.append(("POLE 3  second run finds 0 fixable (found %d)" % len(again),
                      len(again) == 0))

    width = max(len(n) for n, _ in poles)
    for n, ok in poles:
        print("  %-*s  %s" % (width, n, "OBSERVED" if ok else "** FAILED **"))
    bad = [n for n, ok in poles if not ok]
    print("\nself-test: %d/%d poles observed" % (len(poles) - len(bad), len(poles)))
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="repo root (default: cwd)")
    ap.add_argument("--census", action="store_true", help="report the family and exit 0")
    ap.add_argument("--apply", action="store_true", help="write the edits")
    ap.add_argument("--self-test", action="store_true", help="run the negative control")
    ap.add_argument("--paths", nargs="*", help="restrict to these repo-relative paths")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    root = Path(args.root).resolve()
    rels = args.paths if args.paths else tracked_staged(root)
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                          capture_output=True, text=True).stdout.strip() or "?"
    ruff_v = (subprocess.run(["ruff", "--version"], capture_output=True, text=True).stdout.strip()
              if shutil.which("ruff") else "ABSENT")

    fixable, unfixable = collect(root, rels)
    print("BASIS: root=%s HEAD=%s files=%d %s" % (root, head, len(rels), ruff_v))
    print("F821 undefined-name sites: %d fixable, %d unfixable" % (len(fixable), len(unfixable)))

    for rel, lineno, name, module in sorted(fixable):
        print("  FIX   %s:%d  %s -> from %s import %s" % (rel, lineno, name, module, name))
    for rel, lineno, name, why in sorted(unfixable):
        print("  LEAVE %s:%d  %s (%s)" % (rel, lineno, name, why))

    if args.census or not args.apply:
        if not args.census:
            print("\nDRY RUN -- nothing written. Re-run with --apply.")
        return 0

    done = apply_fixes(root, fixable)
    print("\nAPPLIED %d edit(s):" % len(done))
    for rel, name, how in done:
        print("  %s  %s  (%s)" % (rel, name, how))

    touched = sorted({rel for rel, _, _ in done})
    if touched:
        left, _ = collect(root, touched)
        print("\nPOST-CHECK on %d touched file(s): %d fixable F821 remaining"
              % (len(touched), len(left)))
        for rel, lineno, name, _m in left:
            print("  STILL RED %s:%d %s" % (rel, lineno, name))
        if left:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
