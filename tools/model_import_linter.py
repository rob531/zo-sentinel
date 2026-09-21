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


# =============================================================================
# MERGE_AUDIT_2026-08-23 L1 -- the `app.dependency_overrides` cluster
# =============================================================================
# `app.dependency_overrides` is NOT a module. `app` is the package;
# dependency_overrides is an attribute of the FastAPI instance, re-exported by
# app/__init__.py's module __getattr__ so `from app import dependency_overrides`
# works (verified: it returns a dict).
#
# The goose recipe already warns about this in prose -- "There is NO
# app.dependency_overrides module" -- and 15 sites across services/staged/ still
# carry it, the single largest unresolved-import cluster in the tree. That is the
# same argument this module was written for: a hand-crafted linter beats a better
# prompt, because the linter is mechanical and the prompt is advisory.
#
# SCOPE, deliberately: a name is rewritten only when its real home is known. Six
# of the 15 sites import a callable that exists NOWHERE under app/
# (override_get_session, override_dependencies_for_testing) and then CALL it.
# Rewriting those to `from app import dependency_overrides` would leave the name
# unbound -- trading a loud ImportError at import time for a silent NameError at
# call time. That is the exact fail-open shape this audit exists to remove, so
# they are reported for a human instead of "fixed".
PHANTOM_DEP_OVERRIDES = "app.dependency_overrides"

# name imported from the phantom module -> the module it actually lives in
DEP_OVERRIDE_HOMES = {
    "dependency_overrides": "app",      # re-exported by app/__init__.py __getattr__
    "get_session": "app.db",            # the single `def get_session` in app/
    "app": "app.main",                  # the FastAPI instance
}

# The name list stops at a trailing comment: `import X  # note` must rewrite the
# import and preserve the note, not treat "X  # note" as an unparseable list.
IMPORT_DEP_OVERRIDES = re.compile(
    r"^([ \t]*)from[ \t]+" + re.escape(PHANTOM_DEP_OVERRIDES)
    + r"[ \t]+import[ \t]+([^#\n]+?)([ \t]*#[^\n]*)?$",
    re.M)

_IMPORT_NAME = re.compile(r"^([A-Za-z_]\w*)(?:\s+as\s+([A-Za-z_]\w*))?$")


def _parse_import_names(blob: str):
    """['X', 'Y as Z'] -> [(X, None), (Y, Z)]. None if anything is unparseable."""
    out = []
    for part in blob.split(","):
        part = part.strip()
        if not part:
            continue
        m = _IMPORT_NAME.match(part)
        if not m:
            return None
        out.append((m.group(1), m.group(2)))
    return out or None


def scan_dependency_overrides(src: str):
    """Find `from app.dependency_overrides import ...` statements.

    Returns (rewrites, unfixable):
      rewrites  [(old_statement, new_statement)] -- safe, name-preserving
      unfixable [(names, reason)]                -- reported, never rewritten
    """
    rewrites, unfixable = [], []
    for m in IMPORT_DEP_OVERRIDES.finditer(src):
        indent, blob, comment = m.group(1), m.group(2), (m.group(3) or "")
        if blob.lstrip().startswith("(") or blob.rstrip().endswith("\\"):
            unfixable.append((blob.strip(),
                              "multi-line/parenthesised import; not rewritten"))
            continue
        names = _parse_import_names(blob)
        if not names:
            unfixable.append((blob.strip(), "unparseable import list"))
            continue
        unknown = [n for n, _a in names if n not in DEP_OVERRIDE_HOMES]
        if unknown:
            unfixable.append((", ".join(unknown),
                              "no such name exists anywhere under app/ -- NOT rewritten, "
                              "because binding it to `dependency_overrides` would turn an "
                              "ImportError into a NameError at the call site. Define the "
                              "override locally in the self-test."))
            continue
        homes: dict[str, list[str]] = {}
        for n, alias in names:
            homes.setdefault(DEP_OVERRIDE_HOMES[n], []).append(
                n + (" as " + alias if alias else ""))
        lines = ["%sfrom %s import %s" % (indent, h, ", ".join(v))
                 for h, v in sorted(homes.items())]
        lines[-1] += comment          # keep any trailing comment on the last line
        rewrites.append((m.group(0), "\n".join(lines)))
    return rewrites, unfixable


def _substitute_names(src: str, drift: dict[str, str]) -> str:
    """Rename model symbols in CODE ONLY -- never inside strings or comments.

    THE DEFECT THIS REPLACES (#4000)
        The previous line was a whole-file regex:

            new = re.sub(r"\\b%s\\b" % re.escape(wrong), canon, new)

        `re` cannot see Python syntax, so it rewrote the name everywhere it
        appeared -- including inside SQL string literals and docstrings. A module
        holding `"SELECT * FROM McpServerRegistry"` as a deliberate table-name
        string had its SQL silently rewritten by a linter whose entire remit is
        import statements. The autofix corrupted the file it was repairing, and
        the corruption looked exactly like the fix.

        That is also how AP-005 (`module_name_used_as_table_name`) gets worse
        rather than better: a table name and a class name that differ only in
        casing are indistinguishable to a regex and completely distinguishable
        to a tokenizer.

    THE FIX
        `tokenize` labels every token, so a NAME is separable from a STRING and
        from a COMMENT. Only NAME tokens are eligible. The substitution splices
        the source by byte offset rather than round-tripping through
        `untokenize`, so every byte outside a replaced token -- whitespace,
        line endings, formatting -- survives unchanged.

        FAIL SAFE, NOT FAIL OPEN: if the file cannot be tokenized, the ORIGINAL
        source is returned untouched. A linter that cannot parse a file has not
        earned the right to rewrite it, and returning the regex result here
        would reintroduce the defect on exactly the malformed files most likely
        to be harmed by it.
    """
    import io
    import tokenize as _tok

    try:
        toks = list(_tok.generate_tokens(io.StringIO(src).readline))
    except (_tok.TokenError, IndentationError, SyntaxError):
        return src

    lines = src.splitlines(keepends=True)
    starts = []
    off = 0
    for ln in lines:
        starts.append(off)
        off += len(ln)

    edits = []
    for t in toks:
        if t.type != _tok.NAME:
            continue
        canon = drift.get(t.string)
        if not canon or canon == t.string:
            continue
        srow, scol = t.start
        erow, ecol = t.end
        if srow != erow:
            continue
        edits.append((starts[srow - 1] + scol, starts[erow - 1] + ecol, canon))

    if not edits:
        return src
    out = []
    prev = 0
    for a, b, canon in sorted(edits):
        out.append(src[prev:a])
        out.append(canon)
        prev = b
    out.append(src[prev:])
    return "".join(out)


def lint_file(path: str, norm_map: dict[str, str], fix: bool):
    src = _read(path)
    if not src:
        return {"file": path, "drift": {}, "fixed": False,
                "dep_overrides": [], "dep_overrides_unfixable": []}
    drift = scan_text(src, norm_map)
    # FU-159: second, import-scoped pass over the FULL model set. Rewrites are
    # whole-file (a name in an import is also used in the body), but a name only
    # qualifies if it appeared in a `from app.models import ...` statement.
    try:
        drift = {**scan_imports(src, build_map(all_models())), **drift}
    except Exception:
        pass  # never let the widened pass break the original narrow one
    # L1: the app.dependency_overrides cluster (statement rewrite, not a token sub)
    dep_rewrites, dep_unfixable = scan_dependency_overrides(src)

    fixed = False
    new = src
    if drift and fix:
        new = _substitute_names(new, drift)
    if dep_rewrites and fix:
        for old_stmt, new_stmt in dep_rewrites:
            new = new.replace(old_stmt, new_stmt)
    if fix and new != src:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new)
        fixed = True
    return {"file": path, "drift": drift, "fixed": fixed,
            "dep_overrides": dep_rewrites, "dep_overrides_unfixable": dep_unfixable}


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
    dep_hits = [r for r in results if r["dep_overrides"]]
    dep_stuck = [r for r in results if r["dep_overrides_unfixable"]]

    if args.json:
        print(json.dumps({"canonical": sorted(canon), "results": hits,
                          "dep_overrides": dep_hits,
                          "dep_overrides_unfixable": dep_stuck}, indent=2))
    else:
        for r in hits:
            verb = "FIXED" if r["fixed"] else "DRIFT"
            print("[%s] %s" % (verb, r["file"]))
            for wrong, canon_name in sorted(r["drift"].items()):
                print("    %-28s -> %s" % (wrong, canon_name))
        for r in dep_hits:
            verb = "FIXED" if args.fix else "DRIFT"
            print("[%s] %s  (app.dependency_overrides)" % (verb, r["file"]))
            for old_stmt, new_stmt in r["dep_overrides"]:
                print("    %s" % old_stmt.strip())
                print("      -> %s" % new_stmt.strip().replace("\n", "\n         "))
        for r in dep_stuck:
            print("[NEEDS-HUMAN] %s  (app.dependency_overrides)" % r["file"])
            for names, why in r["dep_overrides_unfixable"]:
                print("    %s\n      %s" % (names, why))
        total = sum(len(r["drift"]) for r in hits)
        dep_total = sum(len(r["dep_overrides"]) for r in dep_hits)
        stuck_total = sum(len(r["dep_overrides_unfixable"]) for r in dep_stuck)
        print("\n%s: %d file(s), %d wrong ref(s) across canon=%d model names"
              % ("fixed" if args.fix else "found", len(hits), total, len(canon)))
        print("app.dependency_overrides: %d rewritable site(s) in %d file(s); "
              "%d site(s) need a human"
              % (dep_total, len(dep_hits), stuck_total))

    # check mode: nonzero exit if anything actionable remains.
    # A needs-human site ALWAYS fails, in both modes -- --fix cannot clear it, and
    # a gate that goes green while it stands would be the fail-open shape again.
    if dep_stuck:
        return 1
    if not args.fix and (hits or dep_hits):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
