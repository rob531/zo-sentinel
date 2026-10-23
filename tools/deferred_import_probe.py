#!/usr/bin/env python3
"""Import-probe every entry of tools/reachability_deferred.json and say why.

WHY THIS EXISTS
---------------
`tools/orphanage.py` labels an orphan MOUNTABLE on a STATIC TEXT SCAN -- it
never imports the module. On 2026-09-28 (cycle-0153) that label was measured
against the runtime for the first time: **17 of 28 "promotion candidates" were
not mountable**. The triage arithmetic on #3996 / #4004 / #4005 -- "mount the 34
and the deferred list reaches the cap" -- had been computed on that label.

On 2026-09-29 (cycle-0156) the opposite error showed up: probing ALL 51
deferrals, not just the ones carrying the label, found **17 importable** where
the label had found 12. The label is narrow in both directions, so it is a
candidate set and never a verdict.

This is that probe, as one command. It is REPORT-ONLY: it is wired into no
workflow, adds no gate and no predicate. `reachability_ratchet.py` remains the
single judge of the deferred list.

WHAT IT MEASURES (R1: resolved from the runtime, not from a repo path)
---------------------------------------------------------------------
For each deferred module, in a CHILD interpreter (so one bad import cannot
poison the probe's own process, and a module that calls sys.exit at import
cannot take the census with it):

    importlib.import_module(name)  ->  getattr(mod, "router")

Verdicts:
  MOUNTABLE          imports and exposes a router (route paths listed)
  NO_ROUTER          imports, but exposes no `router` attribute
  MODEL_NAME_WRONG   ImportError naming an app.models class that EXISTS under
                     a different spelling -- a mechanical rename
  MODEL_ABSENT       ImportError naming an app.models class with no referent
                     on any plane -- needs a model or a dropped import
  IMPORT_ERROR       any other ImportError
  MISSING_PACKAGE    ModuleNotFoundError for a third-party package
  IMPORT_CRASH       anything else raised at import time

MODEL_NAME_WRONG vs MODEL_ABSENT is decided by reading the CLASS NAMES OUT OF
app/models.py, never from a hand-written table: a name matches if it equals a
real class name case-insensitively once underscores are stripped. That is the
only inference in the tool and it is stated here so it can be checked (R5).

USAGE
    python tools/deferred_import_probe.py                 # table + summary
    python tools/deferred_import_probe.py --json          # machine-readable
    python tools/deferred_import_probe.py --verdict MOUNTABLE   # names only
    python tools/deferred_import_probe.py --self-test     # the controls

EXIT CODES
    0  the probe ran and every entry got a verdict
    2  the probe could not run (missing deferred file, unreadable models)
It NEVER exits non-zero on a bad verdict. A census that can fail a build is a
gate, and this is not one.
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFERRED_PATH = os.path.join(ROOT, "tools", "reachability_deferred.json")
MODELS_PATH = os.path.join(ROOT, "app", "models.py")

_CHILD = (
    "import json,sys,importlib\n"
    "m=sys.argv[1]\n"
    "try:\n"
    "    mod=importlib.import_module(m)\n"
    "except BaseException as e:\n"
    "    print(json.dumps({'ok':False,'exc':type(e).__name__,"
    "'msg':str(e)[:400]}));raise SystemExit(0)\n"
    "r=getattr(mod,'router',None)\n"
    "print(json.dumps({'ok':r is not None,'exc':None if r is not None else 'NoRouter',"
    "'msg':'' if r is not None else 'module exposes no router attribute',"
    "'routes':[getattr(x,'path',None) for x in getattr(r,'routes',[])]"
    " if r is not None else []}))\n"
)

_IMPORT_NAME_RE = re.compile(
    r"cannot import name ['\"]([^'\"]+)['\"] from ['\"]([^'\"]+)['\"]")


def model_class_names(models_path=None):
    """Every class defined in app/models.py, read with ast -- not imported.

    ast rather than import on purpose: app.models pulls in the DB layer, and a
    census must not need a database to say what names a file defines.
    """
    path = models_path or MODELS_PATH
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    return sorted(n.name for n in tree.body if isinstance(n, ast.ClassDef))


def _key(name):
    return name.replace("_", "").lower()


def classify_missing_name(wanted, module, known):
    """MODEL_NAME_WRONG (+ the real spelling) or MODEL_ABSENT.

    known is the list of real class names. The match is case-insensitive with
    underscores stripped, so MCPServerRegistry / MCP_Server_Registry both
    resolve to McpServerRegistry -- and nothing else does.
    """
    if module != "app.models":
        return "IMPORT_ERROR", None
    hit = [k for k in known if _key(k) == _key(wanted)]
    if hit:
        return "MODEL_NAME_WRONG", hit[0]
    return "MODEL_ABSENT", None


def classify(rec, known):
    """rec is the child's JSON. Returns (verdict, detail)."""
    if rec.get("ok"):
        return "MOUNTABLE", None
    exc, msg = rec.get("exc") or "", rec.get("msg") or ""
    if exc == "NoRouter":
        return "NO_ROUTER", None
    if exc == "ModuleNotFoundError":
        return "MISSING_PACKAGE", msg
    if exc == "ImportError":
        m = _IMPORT_NAME_RE.search(msg)
        if m:
            return classify_missing_name(m.group(1), m.group(2), known)
        return "IMPORT_ERROR", msg
    return "IMPORT_CRASH", "%s: %s" % (exc, msg)


def probe_one(name, python=None, root=None):
    root = root or ROOT
    env = dict(os.environ)
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.run([python or sys.executable, "-c", _CHILD, name],
                       cwd=root, capture_output=True, text=True, timeout=240,
                       env=env)
    for line in reversed((p.stdout or "").splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return {"ok": False, "exc": "ProbeFailed",
            "msg": "child rc=%s %s" % (p.returncode, (p.stderr or "")[-200:])}


def run(names=None, python=None):
    known = model_class_names()
    if names is None:
        with open(DEFERRED_PATH, encoding="utf-8") as fh:
            names = sorted(json.load(fh)["deferred"].keys())
    out = []
    for n in names:
        rec = probe_one(n, python=python)
        verdict, detail = classify(rec, known)
        out.append({"module": n, "verdict": verdict, "detail": detail,
                    "exc": rec.get("exc"), "msg": (rec.get("msg") or "")[:200],
                    "routes": rec.get("routes") or []})
    return out, known


# --------------------------------------------------------------- self-test
def _self_test():
    """Six controls. Each asserts a DEFECT would be visible, not that the
    happy path works -- an assertion never seen red is not evidence (R4)."""
    fails = []

    def check(n, cond, why):
        if not cond:
            fails.append("%d %s" % (n, why))
        print("  %s control %d: %s" % ("ok  " if cond else "FAIL", n, why))

    known = ["McpServerRegistry", "McpLlmAxisScore", "ApiKey", "VulnAdvisory"]

    # 1. a casing-only variant must resolve, and must name the REAL spelling
    v, d = classify_missing_name("MCPServerRegistry", "app.models", known)
    check(1, v == "MODEL_NAME_WRONG" and d == "McpServerRegistry",
          "a casing variant resolves to the real class name")

    # 2. underscores stripped too
    v, d = classify_missing_name("MCP_Server_Registry", "app.models", known)
    check(2, v == "MODEL_NAME_WRONG" and d == "McpServerRegistry",
          "underscore variant resolves")

    # 3. THE DISCRIMINATOR: a name with no referent must NOT be matched to a
    #    near neighbour. AuditLog must never resolve to ApiKey.
    v, d = classify_missing_name("AuditLog", "app.models", known)
    check(3, v == "MODEL_ABSENT" and d is None,
          "an absent class is MODEL_ABSENT, never fuzzed onto a neighbour")

    # 4. a plural is a DIFFERENT name and must not be matched
    v, d = classify_missing_name("McpServerRegistries", "app.models", known)
    check(4, v == "MODEL_ABSENT",
          "a plural is not the same name (no stemming)")

    # 5. an ImportError from a module that is not app.models is not a model
    #    problem -- unknown is not the nearest known thing (R6)
    v, d = classify_missing_name("get_verdict_breakdown",
                                 "verdict_breakdown_api", known)
    check(5, v == "IMPORT_ERROR",
          "a non-app.models ImportError is not classified as a model name")

    # 6. the verdicts must partition: no record may get two, and a router with
    #    zero routes is MOUNTABLE but visibly empty
    r = {"ok": True, "routes": []}
    v, _ = classify(r, known)
    check(6, v == "MOUNTABLE",
          "a router with zero routes is MOUNTABLE with an empty route list")

    # 7. models are read with ast, so the real file must parse and be non-empty
    real = model_class_names()
    check(7, len(real) > 0 and "McpServerRegistry" in real,
          "app/models.py parses and yields real class names (%d)" % len(real))

    print("\n%s  %d/%d" % ("SELF-TEST PASS" if not fails else "SELF-TEST FAIL",
                           7 - len(fails), 7))
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="import-probe the deferred router list (report only)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verdict", help="print only the modules with this verdict")
    ap.add_argument("--module", action="append", help="probe these instead of the file")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    if not os.path.exists(DEFERRED_PATH):
        print("CANNOT MEASURE: no %s" % DEFERRED_PATH, file=sys.stderr)
        return 2
    if not os.path.exists(MODELS_PATH):
        print("CANNOT MEASURE: no %s" % MODELS_PATH, file=sys.stderr)
        return 2

    results, known = run(args.module)

    if args.verdict:
        for r in results:
            if r["verdict"] == args.verdict:
                print(r["module"])
        return 0

    order = ["MOUNTABLE", "NO_ROUTER", "MODEL_NAME_WRONG", "MODEL_ABSENT",
             "IMPORT_ERROR", "MISSING_PACKAGE", "IMPORT_CRASH"]
    counts = {k: 0 for k in order}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    if args.json:
        print(json.dumps({"basis": {"root": ROOT, "deferred": len(results),
                                    "model_classes": len(known)},
                          "counts": counts, "results": results}, indent=1))
        return 0

    print("basis: %d deferral(s) in %s" % (len(results), DEFERRED_PATH))
    print("       %d class(es) defined in app/models.py, read with ast"
          % len(known))
    print("       verdicts resolved by IMPORTING each module in a child "
          "interpreter, not by a text scan\n")
    for v in order:
        rows = [r for r in results if r["verdict"] == v]
        if not rows:
            continue
        print("%s  (%d)" % (v, len(rows)))
        for r in rows:
            extra = ""
            if v == "MOUNTABLE":
                extra = "  " + (", ".join(p for p in r["routes"] if p)
                                or "NO ROUTES -- mounting it adds no surface")
            elif v == "MODEL_NAME_WRONG":
                extra = "  -> %s" % r["detail"]
            elif r["msg"]:
                extra = "  " + r["msg"][:110]
            print("    %-48s%s" % (r["module"], extra))
        print("")
    print("SUMMARY: " + " | ".join("%s=%d" % (k, counts[k])
                                   for k in order if counts[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
