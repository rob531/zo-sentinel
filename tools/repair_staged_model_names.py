#!/usr/bin/env python3
"""
FU-231 / FU-142 — repair builder-generated model-name drift in services/staged.

WHAT THIS IS FOR
----------------
`tools/promote_staged_to_active.py` runs a liveness contract against every staged
service.  As of 2026-08-02 the dominant hold family was
`ImportError: cannot import name X from app.models|app.db` — builder-generated
services importing model names that do not exist.

Measured on main @ b7febe5 (301-308 staged dirs): 181 bad import sites across 66
services, 47 distinct names.  Those 47 split into TWO populations that need
completely different work, and conflating them is what made the family look like
one 132-item problem:

  FAMILY A — CASING / PLURAL / PREFIX DRIFT onto a model that really exists.
             `Orgs`->`Org`, `Users`->`User`, `VulnerabilityLink`->`VulnLink`,
             `ServerRegistry`->`McpServerRegistry`, ...
             Deterministically repairable.  THIS TOOL FIXES THESE.

  FAMILY B — NO REFERENT AT ALL.  `MeshMemory`, `MCPSignalScores`,
             `ServiceHealth`, `CodeNode`, `PerspectiveMembership`, ...
             The service was generated against a schema that was never built.
             No rename can fix these; they need a model + migration, or the
             service is unbuildable as specified.  THIS TOOL ONLY REPORTS THEM.

DESIGN RULES (deliberate, do not "simplify" them away)
-----------------------------------------------------
1. The set of REAL names is derived by AST from `app/models.py` and `app/db.py`
   at run time.  It is never hardcoded, so the tool stays correct as the schema
   moves.  A hardcoded list is how this class of defect gets re-created.
2. A rewrite happens ONLY when the normalisation of the bad name resolves to
   EXACTLY ONE real export.  Ambiguous or unresolved => untouched + reported.
   Guessing is worse than leaving an honest ImportError in place.
3. Lowercase / snake_case names are never mapped.  `vuln_advisories` normalises
   onto `VulnAdvisory`, but a service asking for a lowercase name wants a TABLE,
   not a model class, and swapping one for the other produces a subtler failure
   than the one it cures.
4. A name is only rewritten in a file's body when that file does not itself
   define or assign it.
5. `--apply` is opt-in.  Default is a dry run.  Re-running after a successful
   apply is a no-op (idempotent by character): the second run finds nothing to
   map because the names are already real.

USAGE
    python tools/repair_staged_model_names.py                 # dry run + report
    python tools/repair_staged_model_names.py --apply         # rewrite
    python tools/repair_staged_model_names.py --json          # machine readable
    python tools/repair_staged_model_names.py --self-test     # incl. neg. control

EXIT CODES
    0  no unmapped (family-B) names remain      -- or --apply succeeded
    1  family-B residue remains (informational, expected today)
    2  cannot evaluate (missing app/models.py, unparseable tree, ...)
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_MODULES = {
    "app.models": os.path.join("app", "models.py"),
    "app.db": os.path.join("app", "db.py"),
}
STAGED = os.path.join("services", "staged")

# Explicit, auditable module redirects: the name is real, the module is not.
# Kept tiny and separate from the fuzzy matcher on purpose -- every entry here
# is a claim someone can check by hand.
MODULE_REDIRECTS = {
    ("app.db", "StaticPool"): "sqlalchemy.pool",
    ("app.models", "StaticPool"): "sqlalchemy.pool",
}

# Synonym folding applied during normalisation.  Additive only.
SYNONYMS = [("vulnerability", "vuln"), ("mcpmcp", "mcp")]


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------
def _base_key(name: str) -> str:
    k = re.sub(r"[^a-z0-9]", "", name.lower())
    for a, b in SYNONYMS:
        k = k.replace(a, b)
    return k


def _depluralise(k: str) -> str:
    if k.endswith("ies") and len(k) > 4:
        return k[:-3] + "y"
    if k.endswith("ses") or k.endswith("xes") or k.endswith("zes"):
        return k[:-2]
    if k.endswith("s") and not k.endswith("ss"):
        return k[:-1]
    return k


def keys_for(name: str) -> set[str]:
    """Every normalised form under which `name` may legitimately be recognised."""
    out: set[str] = set()
    for k in (_base_key(name), _depluralise(_base_key(name))):
        if not k:
            continue
        out.add(k)
        if k.startswith("mcp"):
            out.add(k[3:])
        else:
            out.add("mcp" + k)
    return {k for k in out if k}


def is_capwords(name: str) -> bool:
    return bool(name) and name[0].isupper() and "_" not in name


# --------------------------------------------------------------------------
# reading the real world
# --------------------------------------------------------------------------
def module_exports(path: str) -> set[str]:
    """Top-level names a `from <mod> import X` could legally bind."""
    tree = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
    return names


def build_index(exports: set[str]) -> dict[str, set[str]]:
    """normalised key -> {real names carrying that key}."""
    idx: dict[str, set[str]] = defaultdict(set)
    for real in exports:
        if not is_capwords(real):
            continue  # only model-ish CapWords names participate in fuzzy repair
        for k in keys_for(real):
            idx[k].add(real)
    return idx


def resolve(bad: str, idx: dict[str, set[str]]) -> str | None:
    """Rule 2 + rule 3: unique resolution only, CapWords only."""
    if not is_capwords(bad):
        return None
    hits: set[str] = set()
    for k in keys_for(bad):
        hits |= idx.get(k, set())
    hits.discard(bad)
    return hits.pop() if len(hits) == 1 else None


# --------------------------------------------------------------------------
# file surgery
# --------------------------------------------------------------------------
def locally_defined(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
    return out


def plan_file(path: str, pools: dict[str, set[str]], idxs: dict[str, dict[str, set[str]]]):
    """Return (rewrites, unmapped, redirects) for one file. Pure -- writes nothing."""
    src = io.open(path, encoding="utf-8", errors="replace").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None, None, None
    local = locally_defined(tree)
    rewrites: dict[str, str] = {}
    redirects: list[tuple[str, str, str]] = []  # (module, name, new_module)
    unmapped: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module not in pools:
            continue
        for alias in node.names:
            nm = alias.name
            if nm == "*" or nm in pools[node.module]:
                continue
            if (node.module, nm) in MODULE_REDIRECTS:
                redirects.append((node.module, nm, MODULE_REDIRECTS[(node.module, nm)]))
                continue
            target = resolve(nm, idxs[node.module])
            if target and nm not in local:
                rewrites[nm] = target
            else:
                unmapped.append((node.module, nm))
    return rewrites, unmapped, redirects


def apply_file(path: str, rewrites: dict[str, str], redirects) -> bool:
    src = io.open(path, encoding="utf-8", errors="replace").read()
    out = src
    for old, new in rewrites.items():
        out = re.sub(r"\b%s\b" % re.escape(old), new, out)
    for module, name, new_module in redirects:
        # drop `name` from the app.* import, then add a correct import line.
        def _strip(m):
            head, body = m.group(1), m.group(2)
            parts = [p.strip() for p in body.replace("(", "").replace(")", "").split(",")]
            parts = [p for p in parts if p and p.split()[0] != name]
            return "" if not parts else "%s%s" % (head, ", ".join(parts))

        out = re.sub(
            r"(from\s+%s\s+import\s+)([^\n]+)" % re.escape(module), _strip, out
        )
        line = "from %s import %s\n" % (new_module, name)
        if line not in out:
            out = re.sub(r"^(\s*\n)*", lambda m: m.group(0) + line, out, count=1)
    out = re.sub(r"\n{4,}", "\n\n\n", out)
    if out == src:
        return False
    try:
        ast.parse(out)  # never write a file we just broke
    except SyntaxError:
        sys.stderr.write("REFUSED (would not parse): %s\n" % path)
        return False
    io.open(path, "w", encoding="utf-8", newline="").write(out)
    return True


# --------------------------------------------------------------------------
def run(repo: str, apply: bool):
    pools, idxs = {}, {}
    for mod, rel in SOURCE_MODULES.items():
        p = os.path.join(repo, rel)
        if not os.path.exists(p):
            return {"error": "missing %s" % rel, "rc": 2}
        pools[mod] = module_exports(p)
        idxs[mod] = build_index(pools[mod])

    staged = os.path.join(repo, STAGED)
    if not os.path.isdir(staged):
        return {"error": "missing %s" % STAGED, "rc": 2}

    mapped = Counter()
    unmapped = Counter()
    redirected = Counter()
    svc_mapped, svc_unmapped = set(), set()
    changed_files = []
    unparseable = 0

    for root, _dirs, files in os.walk(staged):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            svc = os.path.relpath(path, staged).split(os.sep)[0]
            rw, un, rd = plan_file(path, pools, idxs)
            if rw is None:
                unparseable += 1
                continue
            for old, new in rw.items():
                mapped["%s -> %s" % (old, new)] += 1
                svc_mapped.add(svc)
            for mod, nm in un:
                unmapped["%s.%s" % (mod, nm)] += 1
                svc_unmapped.add(svc)
            for mod, nm, newmod in rd:
                redirected["%s.%s -> %s" % (mod, nm, newmod)] += 1
                svc_mapped.add(svc)
            if apply and (rw or rd) and apply_file(path, rw, rd):
                changed_files.append(os.path.relpath(path, repo))

    return {
        "rc": 1 if unmapped else 0,
        "staged_services": len([d for d in os.listdir(staged)
                                if os.path.isdir(os.path.join(staged, d))]),
        "unparseable_files": unparseable,
        "family_a_repairable_sites": sum(mapped.values()) + sum(redirected.values()),
        "family_a_services": len(svc_mapped),
        "family_b_unmapped_sites": sum(unmapped.values()),
        "family_b_services": len(svc_unmapped),
        "family_b_distinct": len(unmapped),
        "mapped": dict(mapped.most_common()),
        "module_redirects": dict(redirected.most_common()),
        "unmapped": dict(unmapped.most_common()),
        "changed_files": changed_files,
        "applied": apply,
    }


# --------------------------------------------------------------------------
def self_test() -> int:
    """Positive AND negative controls. An assertion never seen fail is not evidence."""
    idx = build_index({"Org", "User", "McpServerRegistry", "VulnAdvisory",
                       "VulnLink", "McpLlmAxisScore", "PerspectiveEvent",
                       "CadenceJobRun", "AskCorpusDoc", "Base", "Integer"})
    ok = True

    def check(label, got, want):
        nonlocal ok
        if got != want:
            ok = False
            print("FAIL %-34s got=%r want=%r" % (label, got, want))
        else:
            print("pass %-34s -> %r" % (label, got))

    # positive controls -- the drift shapes actually observed in services/staged
    check("Orgs", resolve("Orgs", idx), "Org")
    check("Users", resolve("Users", idx), "User")
    check("ServerRegistry", resolve("ServerRegistry", idx), "McpServerRegistry")
    check("VulnerabilityAdvisory", resolve("VulnerabilityAdvisory", idx), "VulnAdvisory")
    check("VulnerabilityLink", resolve("VulnerabilityLink", idx), "VulnLink")
    check("LlmAxisScore", resolve("LlmAxisScore", idx), "McpLlmAxisScore")
    check("McpLlmAxisScores", resolve("McpLlmAxisScores", idx), "McpLlmAxisScore")
    check("CadenceJobRuns", resolve("CadenceJobRuns", idx), "CadenceJobRun")
    check("McpPerspectiveEvent", resolve("McpPerspectiveEvent", idx), "PerspectiveEvent")
    check("VulnAdvisories", resolve("VulnAdvisories", idx), "VulnAdvisory")

    # NEGATIVE CONTROLS -- these MUST NOT be rewritten (rule 2 / rule 3).
    # Without these the tool would look like it works while inventing mappings.
    check("MeshMemory (no referent)", resolve("MeshMemory", idx), None)
    check("MCPSignalScores (no referent)", resolve("MCPSignalScores", idx), None)
    check("ServiceHealth (no referent)", resolve("ServiceHealth", idx), None)
    check("CodeNode (no referent)", resolve("CodeNode", idx), None)
    check("AskCorpusIndex (Index!=Doc)", resolve("AskCorpusIndex", idx), None)
    check("vuln_advisories (snake_case)", resolve("vuln_advisories", idx), None)
    check("mcp_signal_scores (snake_case)", resolve("mcp_signal_scores", idx), None)
    check("ORGS_TABLE (constant)", resolve("ORGS_TABLE", idx), None)
    check("Org (already correct)", resolve("Org", idx), None)

    # ambiguity control: two real names sharing a key must resolve to neither
    amb = build_index({"Score", "McpScore"})
    check("ambiguous Scores", resolve("Scores", amb), None)

    print("\nSELF-TEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--apply", action="store_true", help="write the repairs (default: dry run)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return self_test()

    res = run(a.repo, a.apply)
    if a.json:
        print(json.dumps(res, indent=2))
        return res.get("rc", 2)
    if res.get("error"):
        print("CANNOT EVALUATE:", res["error"])
        return 2

    print("staged services: %d   (unparseable files: %d)"
          % (res["staged_services"], res["unparseable_files"]))
    print("mode: %s" % ("APPLIED" if res["applied"] else "DRY RUN"))
    print()
    print("FAMILY A -- repairable name drift : %3d sites across %3d services"
          % (res["family_a_repairable_sites"], res["family_a_services"]))
    for k, v in res["mapped"].items():
        print("    %3d  %s" % (v, k))
    for k, v in res["module_redirects"].items():
        print("    %3d  %s   [module redirect]" % (v, k))
    print()
    print("FAMILY B -- NO REFERENT, not repairable by rename : %3d sites / %d distinct / %d services"
          % (res["family_b_unmapped_sites"], res["family_b_distinct"], res["family_b_services"]))
    for k, v in res["unmapped"].items():
        print("    %3d  %s" % (v, k))
    if res["changed_files"]:
        print("\nfiles written: %d" % len(res["changed_files"]))
    return res["rc"]


if __name__ == "__main__":
    sys.exit(main())
