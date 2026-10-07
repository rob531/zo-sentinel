#!/usr/bin/env python3
"""staged_module_shape_census.py -- how many staged "modules" are not modules?

[c191, 2026-10-07] THE CLASS, AND WHY NOTHING COULD SEE IT
-----------------------------------------------------------
A staged service's halves are emitted by independent builder directives. Some
of those directives returned their own *manifest output* instead of a module
body, and it was written to disk as the module. The live example, and the one
that forced this tool into existence, is `services/staged/cve_risk_summary/
logic.py`, whose entire content is four file paths:

    services/staged/cve_risk_summary/contract.py
    services/staged/cve_risk_summary/router.py
    services/staged/cve_risk_summary/service.toml
    services/_exemplar/logic.py

That file is **syntactically valid Python**: each line parses as division
between undefined names. So `py_compile` passes it, every parse-based
instrument passes it, and `repair_staged_sibling_symbols.py` read it as "a
module that merely does not export the wanted name" -- and classified the
router's import as PROVEN_MODULE, repointable at `.contract`. Applying that
would have greened the promoter's import gate over a service with no logic
body at all: a Potemkin service, passed by a gate, its census site erased.
HARNESS_DOCTRINE R6: unknown is not zero, and a file that parses is not a
module.

The import gate (`promote_staged_to_active.py::_import_check`) DOES still catch
this particular one, because the listing raises NameError at import time. That
is why #4002 calls the population a backlog and not an active hazard. But the
gate reports it as "unresolved import", which is the wrong diagnosis and the
wrong cure: the defect is a missing module body, and the only honest repair is
to re-emit the directive.

WHAT IS COUNTED (and the shape, per R5)
    NOT_PARSEABLE  the file does not compile at all
    PATH_LISTING   >=50% of non-blank lines are bare file paths
    TREE_ART       box-drawing characters in the first 4 KiB
    NO_CODE        parses, but carries no top-level import/def/class/decorator
    EMPTY          no non-blank lines
    `__init__.py` is EXEMPT from NO_CODE and EMPTY -- an empty package marker is
    legitimate, and counting it would bury the real class under ~500 of them.
    `services/staged/staged_backup/` is EXCLUDED: it is a backup tree, not the
    promotable population. Both exclusions are printed with the number.

BASIS
    Resolved from --root, and --root should be the RUNTIME tree, not a repo
    checkout (R1). The runtime holds untracked staged directories a clean
    checkout does not, and the promoter runs against the runtime.

PREDICATE
    --max N exits 1 when the count exceeds N, 0 when it does not, and 2 when it
    could not evaluate. Never 0 on an error.

NEGATIVE CONTROL (R4)
    --self-test builds a throwaway tree with one file of each class plus three
    that must NOT be counted (a real module, an empty __init__.py, a backup-tree
    file) and asserts both directions. It exits 1 if any pole misses.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import tempfile
import shutil

PATH_LINE = re.compile(r"^\s*[\w./-]+\.(py|toml|json|md|ya?ml|txt|sh|cfg|ini)\s*$")
BOX_ART = re.compile(r"[─-╿]")
TOPLEVEL = re.compile(r"^(import |from |def |class |@|async def )", re.M)

CLASSES = ("NOT_PARSEABLE", "PATH_LISTING", "TREE_ART", "NO_CODE", "EMPTY")


def classify(src: str, basename: str):
    """None when the file is a plausible module body, else its class name."""
    try:
        compile(src.encode("utf-8", "replace"), "<census>", "exec")
    except SyntaxError:
        return "NOT_PARSEABLE"
    except Exception:
        return "NOT_PARSEABLE"
    lines = [ln for ln in src.splitlines() if ln.strip()]
    if not lines:
        return None if basename == "__init__.py" else "EMPTY"
    if TOPLEVEL.search(src):
        return None
    if BOX_ART.search(src[:4096]):
        return "TREE_ART"
    if sum(1 for ln in lines if PATH_LINE.match(ln)) >= max(1, int(0.5 * len(lines))):
        return "PATH_LISTING"
    return None if basename == "__init__.py" else "NO_CODE"


def census(root: str):
    base = os.path.join(root, "services", "staged")
    if not os.path.isdir(base):
        return None, "services/staged not found under %s" % root
    rows, scanned = [], 0
    for d, dirnames, files in os.walk(base):
        rel_d = os.path.relpath(d, root).replace(os.sep, "/")
        if "/staged_backup" in "/" + rel_d:
            dirnames[:] = []
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            scanned += 1
            p = os.path.join(d, f)
            try:
                src = open(p, encoding="utf-8", errors="replace").read()
            except OSError as e:
                rows.append((os.path.relpath(p, root).replace(os.sep, "/"),
                             "NOT_PARSEABLE", "unreadable: %r" % e))
                continue
            k = classify(src, f)
            if k:
                first = next((ln.strip() for ln in src.splitlines() if ln.strip()), "")
                rows.append((os.path.relpath(p, root).replace(os.sep, "/"), k, first[:60]))
    return {"scanned": scanned, "rows": sorted(rows)}, None


def _svc(root, name, files):
    d = os.path.join(root, "services", "staged", name)
    os.makedirs(d, exist_ok=True)
    for fn, body in files.items():
        open(os.path.join(d, fn), "w", encoding="utf-8").write(body)


def self_test() -> int:
    tmp = tempfile.mkdtemp(prefix="shape_selftest_")
    poles, failed = [], 0

    def pole(name, ok, detail=""):
        nonlocal failed
        poles.append((name, ok, detail))
        if not ok:
            failed += 1
    try:
        # MUST be counted -- one of each class
        _svc(tmp, "s_listing", {"logic.py": "services/staged/s_listing/contract.py\n"
                                            "services/staged/s_listing/router.py\n"})
        _svc(tmp, "s_broken", {"logic.py": "def f(\n"})
        # tree art that PARSES (it sits in a docstring). Art that does NOT
        # parse is already NOT_PARSEABLE, and that class takes precedence.
        _svc(tmp, "s_tree", {"logic.py": '''"""\n\u251c\u2500\u2500 a\n\u2514\u2500\u2500 b\n"""\n'''})
        _svc(tmp, "s_nocode", {"logic.py": "{\n}\n"})
        _svc(tmp, "s_empty", {"logic.py": "\n\n"})
        # MUST NOT be counted
        _svc(tmp, "s_real", {"logic.py": "import os\n\n\ndef g():\n    return os.sep\n",
                             "__init__.py": ""})
        os.makedirs(os.path.join(tmp, "services", "staged", "staged_backup", "s_bk"),
                    exist_ok=True)
        open(os.path.join(tmp, "services", "staged", "staged_backup", "s_bk",
                          "logic.py"), "w").write("services/x/y.py\n")

        rep, err = census(tmp)
        pole("0 census ran", rep is not None and not err, detail=str(err))
        got = {r.split("/")[2]: k for r, k, _ in rep["rows"]}
        for svc, want in (("s_listing", "PATH_LISTING"), ("s_broken", "NOT_PARSEABLE"),
                          ("s_tree", "TREE_ART"), ("s_nocode", "NO_CODE"),
                          ("s_empty", "EMPTY")):
            pole("counts %s as %s" % (svc, want), got.get(svc) == want,
                 detail="got %s" % got.get(svc))
        # The three that must stay out. Each is a direction the census could get
        # wrong in a way that would INFLATE the headline, so each is a pole.
        pole("RED-ON-PURPOSE: a real module is NOT counted",
             "s_real" not in got, detail="got %s" % got.get("s_real"))
        pole("RED-ON-PURPOSE: an empty __init__.py is NOT counted",
             not any(r.endswith("s_real/__init__.py") for r, _, _ in rep["rows"]))
        pole("RED-ON-PURPOSE: staged_backup/ is EXCLUDED",
             not any("/staged_backup/" in r for r, _, _ in rep["rows"]),
             detail=str([r for r, _, _ in rep["rows"] if "staged_backup" in r]))
        # The whole reason this tool exists: the listing PARSES.
        listing = "services/staged/s_listing/contract.py\n"
        ok_parses = True
        try:
            compile(listing, "<t>", "exec")
        except SyntaxError:
            ok_parses = False
        pole("RED-ON-PURPOSE: a path listing COMPILES, so py_compile is blind",
             ok_parses and classify(listing, "logic.py") == "PATH_LISTING",
             detail="parses=%s class=%s" % (ok_parses, classify(listing, "logic.py")))
        # predicate direction
        pole("--max predicate discriminates",
             len(rep["rows"]) > 0, detail=str(len(rep["rows"])))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for name, ok, detail in poles:
        print("  %-4s %s%s" % ("PASS" if ok else "FAIL", name,
                               ("  <- " + detail) if detail and not ok else ""))
    print("%d/%d poles observed (4 of them RED-ON-PURPOSE exclusions)"
          % (len(poles) - failed, len(poles)))
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default="/home/workspace/zo_sentinel",
                    help="RUNTIME tree, not a checkout (R1)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true", help="print every counted file")
    ap.add_argument("--max", type=int, default=None,
                    help="exit 1 when the count EXCEEDS this bound")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    rep, err = census(a.root)
    if err:
        print("CANNOT EVALUATE: %s" % err)
        return 2
    rows = rep["rows"]
    by = collections.Counter(k for _, k, _ in rows)
    svcs = {r.split("/")[2] for r, _, _ in rows}
    if a.json:
        print(json.dumps({"basis": {"root": a.root, "scanned": rep["scanned"],
                                    "excludes": ["services/staged/staged_backup/",
                                                 "__init__.py for NO_CODE/EMPTY"]},
                          "not_a_module": len(rows), "services": len(svcs),
                          "classes": dict(by),
                          "files": [{"file": r, "class": k, "first_line": s}
                                    for r, k, s in rows]}, indent=1))
    else:
        print("BASIS root=%s  staged .py scanned=%d  (excludes staged_backup/, and "
              "__init__.py for NO_CODE/EMPTY)" % (a.root, rep["scanned"]))
        print("NOT A MODULE: %d file(s) across %d service(s)" % (len(rows), len(svcs)))
        for k in CLASSES:
            if by.get(k):
                print("  %-14s %d" % (k, by[k]))
        if a.list:
            for r, k, s in rows:
                print("  %-62s %-14s %s" % (r, k, s))
    if a.max is not None:
        if len(rows) > a.max:
            print("RED: not-a-module %d > bound %d" % (len(rows), a.max))
            return 1
        print("GREEN: not-a-module %d <= bound %d" % (len(rows), a.max))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
