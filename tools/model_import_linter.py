#!/usr/bin/env python3
"""model_import_linter.py -- the FU-031 harness linter (Harness Engineering).

WHY THIS EXISTS
---------------
The FU-031 probe (builder_selftest_integrity_report) proved that ~84% of builder
acceptance self-tests degrade to Tier-0, and that ONE shared cause dominates: the
builder emits the WRONG CASING / a spurious plural for app.models symbols --
`MCPServerRegistry`, `MCPLLMAxisScore`, `McpLlmAxisScores`, `MCPScoreDispute`,
`MCPServerSubmission` -- when the real classes are `McpServerRegistry`,
`McpLlmAxisScore`, `McpScoreDispute`, `McpServerSubmission`. The import raises,
the self-test can't run, it degrades, and presence passes for correctness.

The recipe ALREADY names the correct classes and the builder still gets it wrong,
which is the Harness-Engineering point exactly: the fix is not a better prompt, it
is a hand-crafted LINTER that mechanically corrects the drift so the self-test can
actually RUN (after which the self-test still judges real correctness). This is
that linter. It is deliberately narrow -- it only touches DISTINCTIVE `Mcp*`
model names, never short/common names (User/Org/Base/ApiKey) -- so autofix is
unambiguous and false-positive-free.

  canonical set : `class Mcp\\w+` defs parsed from app/models.py (no import)
  a token is WRONG if norm(token) == norm(canonical) but token != canonical,
    where norm(x) = x.lower() with one optional trailing 's' removed
    (catches casing AND the spurious plural).

MODES
  python tools/model_import_linter.py --check <file|dir> ...   # exit 1 if any drift
  python tools/model_import_linter.py --fix   <file|dir> ...   # rewrite in place
  python tools/model_import_linter.py --json  <file|dir> ...

Pure stdlib static. No import of the target, no network. Safe in CI / gate /
emission-time. Intended placements: (a) autofix at EMISSION time (right after the
builder writes a file, before the self-test) -- where FU-031 bites; (b) --check in
the staged->active promotion gate, so a wrong-cased service HOLDs with a clear,
autofixable reason instead of a raw ImportError.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.join(ROOT, "app", "models.py")

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CLASS_DEF = re.compile(r"^\s*class\s+(\w+)\s*[(:]", re.M)


def _read(p):
    try:
        return open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def _norm(tok: str) -> str:
    """lower(), drop underscores, drop one optional trailing 's'.

    FU-159: underscores were NOT dropped before, so `mcp_server_registry` could
    never normalise onto `McpServerRegistry` and the snake_case-table-name-as-a-
    model-class family was structurally unrepairable. Measured live: that family
    plus the spurious plural accounted for 9 of 39 post-#2177 Tier-0 degradations.
    """
    t = tok.lower().replace("_", "")
    return t[:-1] if t.endswith("s") else t


def canonical_models(models_path: str = MODELS) -> set[str]:
    """DISTINCTIVE model class names from app/models.py: `class Mcp\\w+`.

    Distinctive (>=8 chars, Mcp-prefixed) so autofix can never collide with a
    short common identifier. If two canon names normalise the same, both are
    dropped (ambiguous -> never autofix)."""
    names = [n for n in CLASS_DEF.findall(_read(models_path))
             if n.startswith("Mcp") and len(n) >= 8]
    seen, ambiguous = {}, set()
    for n in names:
        k = _norm(n)
        if k in seen and seen[k] != n:
            ambiguous.add(k)
        seen[k] = n
    return {n for n in names if _norm(n) not in ambiguous}


def build_map(canon: set[str]) -> dict[str, str]:
    """norm-key -> canonical spelling."""
    return {_norm(c): c for c in canon}


ALL_CLASS_DEF = re.compile(r"^\s*class\s+(\w+)\s*[(:]", re.M)
IMPORT_FROM_MODELS = re.compile(
    r"from\s+app\.models\s+import\s+(?:\(([^)]*)\)|([^\n(]+))")


def all_models(models_path: str = MODELS) -> set[str]:
    """EVERY class in app/models.py, not just the distinctive `Mcp*` ones.

    Only ever used by `scan_imports`, where the surrounding statement is
    `from app.models import ...` and the intent is therefore unambiguous. The
    whole-file `scan_text` path keeps the original distinctive-only set so its
    false-positive-free guarantee is unchanged.
    """
    names = ALL_CLASS_DEF.findall(_read(models_path))
    seen, ambiguous = {}, set()
    for n in names:
        k = _norm(n)
        if k in seen and seen[k] != n:
            ambiguous.add(k)
        seen[k] = n
    return {n for n in names if _norm(n) not in ambiguous}


def scan_imports(src: str, full_norm_map: dict[str, str]) -> dict[str, str]:
    """Drift found INSIDE `from app.models import ...` statements only.

    Scoping to the import statement is what makes it safe to use short names like
    `Org` here: a token in that position must be a model, so `Orgs -> Org` cannot
    collide with an unrelated identifier elsewhere in the corpus.
    """
    canon_exact = set(full_norm_map.values())
    out: dict[str, str] = {}
    for m in IMPORT_FROM_MODELS.finditer(src):
        for tok in IDENT.findall(m.group(1) or m.group(2) or ""):
            if tok in canon_exact:
                continue
            canon = full_norm_map.get(_norm(tok))
            if canon and tok != canon:
                out[tok] = canon
    return out


def scan_text(src: str, norm_map: dict[str, str]) -> dict[str, str]:
    """Return {wrong_token: canonical} found in src (token != canonical but
    normalises to a canonical model name)."""
    canon_exact = set(norm_map.values())
    out: dict[str, str] = {}
    for tok in set(IDENT.findall(src)):
        if tok in canon_exact:
            continue
        canon = norm_map.get(_norm(tok))
        if canon and tok != canon:
            out[tok] = canon
    return out


def lint_file(path: str, norm_map: dict[str, str], fix: bool):
    src = _read(path)
    if not src:
        return {"file": path, "drift": {}, "fixed": False}
    drift = scan_text(src, norm_map)
    # FU-159: second, import-scoped pass over the FULL model set. Rewrites are
    # whole-file (a name in an import is also used in the body), but a name only
    # qualifies if it appeared in a `from app.models import ...` statement.
    try:
        drift = {**scan_imports(src, build_map(all_models())), **drift}
    except Exception:
        pass  # never let the widened pass break the original narrow one
    fixed = False
    if drift and fix:
        new = src
        for wrong, canon in drift.items():
            new = re.sub(r"\b%s\b" % re.escape(wrong), canon, new)
        if new != src:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(new)
            fixed = True
    return {"file": path, "drift": drift, "fixed": fixed}


def _iter_py(targets):
    for t in targets:
        if os.path.isdir(t):
            for dp, _d, files in os.walk(t):
                if "__pycache__" in dp:
                    continue
                for fn in files:
                    if fn.endswith(".py"):
                        yield os.path.join(dp, fn)
        elif t.endswith(".py"):
            yield t


def main(argv=None):
    ap = argparse.ArgumentParser(description="FU-031 model-name casing linter.")
    ap.add_argument("targets", nargs="+", help="files or dirs to lint")
    ap.add_argument("--fix", action="store_true", help="rewrite in place (else check)")
    ap.add_argument("--check", action="store_true", help="report only (default when --fix absent)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--models", default=MODELS, help="path to app/models.py")
    args = ap.parse_args(argv)

    canon = canonical_models(args.models)
    norm_map = build_map(canon)
    if not canon:
        print("WARN: no canonical Mcp* models found in %s" % args.models, file=sys.stderr)

    results = [lint_file(p, norm_map, args.fix) for p in _iter_py(args.targets)]
    hits = [r for r in results if r["drift"]]

    if args.json:
        print(json.dumps({"canonical": sorted(canon), "results": hits}, indent=2))
    else:
        for r in hits:
            verb = "FIXED" if r["fixed"] else "DRIFT"
            print("[%s] %s" % (verb, r["file"]))
            for wrong, canon_name in sorted(r["drift"].items()):
                print("    %-28s -> %s" % (wrong, canon_name))
        total = sum(len(r["drift"]) for r in hits)
        print("\n%s: %d file(s), %d wrong ref(s) across canon=%d model names"
              % ("fixed" if args.fix else "found", len(hits), total, len(canon)))

    # check mode: nonzero exit if drift remains (fix mode: exit 0 once corrected)
    if not args.fix and hits:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
