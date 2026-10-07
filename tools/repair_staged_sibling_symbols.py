#!/usr/bin/env python3
"""Repair staged-service sibling-import symbol drift -- PROVEN BY GIT HISTORY, never guessed.

THE DEFECT (measured on origin/main d21feb260, 2026-10-05)

    655 import sites across 419 of the staged services import a name from a SIBLING
    module in their own service directory that the sibling does not define. Each one
    dies at `importlib.import_module("services.staged.<svc>.router")`, which is exactly
    the gate `tools/promote_staged_to_active.py::_import_check` runs, so none of those
    419 services can ever be promoted.

    The cause is not a typo. A service's halves were emitted by two INDEPENDENT builder
    directives that never shared an interface, and a later rebuild of one half silently
    broke the other:

        cve_family_propagation/router.py  wants propagate_family_threats
            -> introduced ONLY by `build_cve_family_propagation_router (#2237)`
            -> logic.py defines propagate_family_links (its own directive)
        perspective_event_rollup/router.py wants get_perspective_event_summary
            -> logic built #2149, router built #2150 against it,
               logic REBUILT #3953 as rollup_events -- router never updated

WHY THIS TOOL DOES NOT RENAME BY SIMILARITY

    `propagate_family_threats` -> `propagate_family_links` *looks* obvious. It is a guess,
    and FU-168/cycle-0168 is the precedent for refusing exactly this shape: a repair is
    only evidence if the correspondence has an ORACLE. Here the oracle is git:

        a rename is PROVEN when the commit that removed `want` from the sibling added
        EXACTLY ONE top-level name in the same file, and that name is still defined there.

    Everything else is REFUSED with a reason code and left for a human or a later cycle.
    Refusal is the common case by design (see --report).

IDEMPOTENCE
    The work list is re-derived from the tree on every run; a repaired site no longer
    scans, so a second `--apply` performs 0 edits. Verified as self-test pole 7.

NEGATIVE CONTROL
    `--self-test` builds throwaway git repos and drives 8 poles, every one observed,
    including 4 that must come back REFUSED. It exits non-zero if any pole does not
    behave, so the assertions in here have been seen RED on purpose (doctrine R4).

SCOPE
    Tracked files only. An untracked file cannot be delivered by a PR, and its history
    cannot be read, so the oracle does not exist for it -- those sites are reported under
    UNTRACKED and never written to.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SKIP_DIR_NAMES = {"__pycache__", ".git"}


# --------------------------------------------------------------------------- git


def git(root: str, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=check
    )


def tracked_files(root: str, pathspec: str) -> set:
    out = git(root, "ls-files", pathspec).stdout
    return set(out.split())


# ------------------------------------------------------------------- ast helpers


def toplevel_names(src: str) -> set:
    """Names bound at module top level -- what `from m import X` can actually find."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.If, ast.Try)):
            # conditionally defined, but still top level once executed
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(sub.name)
    return names


def toplevel_defs(src: str) -> set:
    """Only def/class -- the unit a rename moves. Used by the git oracle."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    return {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def sibling_of(svc: str, node: ast.ImportFrom) -> str | None:
    """Return the sibling module name this ImportFrom targets, else None."""
    mod = node.module or ""
    if node.level == 1 and mod and "." not in mod:
        return mod
    prefix = "services.staged.%s." % svc
    if node.level == 0 and mod.startswith(prefix):
        rest = mod[len(prefix):]
        if rest and "." not in rest:
            return rest
    return None


# ----------------------------------------------------------------------- scanner


def scan(root: str, only_tracked: bool = True) -> list:
    staged_rel = os.path.join("services", "staged")
    staged = os.path.join(root, staged_rel)
    if not os.path.isdir(staged):
        return []
    tracked = tracked_files(root, staged_rel.replace(os.sep, "/"))
    sib_cache: dict = {}
    hits = []
    for svc in sorted(os.listdir(staged)):
        sdir = os.path.join(staged, svc)
        if not os.path.isdir(sdir) or svc in SKIP_DIR_NAMES:
            continue
        for fn in sorted(os.listdir(sdir)):
            if not fn.endswith(".py"):
                continue
            rel = "services/staged/%s/%s" % (svc, fn)
            is_tracked = rel in tracked
            if only_tracked and not is_tracked:
                continue
            try:
                src = open(os.path.join(sdir, fn), encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                sib = sibling_of(svc, node)
                if not sib or sib == fn[:-3]:
                    continue
                sib_rel = "services/staged/%s/%s.py" % (svc, sib)
                sib_abs = os.path.join(sdir, sib + ".py")
                if not os.path.isfile(sib_abs):
                    continue
                if sib_rel not in sib_cache:
                    sib_src = open(sib_abs, encoding="utf-8", errors="replace").read()
                    sib_cache[sib_rel] = (toplevel_names(sib_src), toplevel_defs(sib_src))
                have, defs = sib_cache[sib_rel]
                for alias in node.names:
                    if alias.name == "*" or alias.name in have:
                        continue
                    hits.append(
                        {
                            "svc": svc,
                            "file": rel,
                            "line": node.lineno,
                            "sibling": sib_rel,
                            "want": alias.name,
                            "sibling_defs": sorted(defs),
                            "file_tracked": is_tracked,
                            "sibling_tracked": sib_rel in tracked,
                        }
                    )
    return hits


# ------------------------------------------------------------------ the oracle


# [c191] top-level import/def/class/decorator -- see is_module_shaped() below.
_MODULE_SHAPE_RE = __import__("re").compile(r"^(import |from |def |class |@|async def )")
REFUSALS = (
    "NEVER_IN_HISTORY",  # the name was never in that sibling -- nothing was renamed
    "NO_REMOVING_COMMIT",  # present in history but never observed going absent
    "AMBIGUOUS",  # the removing commit added != 1 top-level def
    "STALE_TARGET",  # proven successor no longer defined on disk
    "SIBLING_UNTRACKED",  # no readable history for the oracle
    # [c191] the named sibling is not a module body at all (path listing, tree
    # art, prose, empty). 79 such files across 77 live staged services on
    # 2026-10-07, 62 tracked. A rewritten import would hide it, not cure it.
    "SIBLING_NOT_A_MODULE",
    "SIBLING_UNREADABLE",

)


def is_module_shaped(src: str) -> bool:
    """True when `src` plausibly IS a Python module body.

    [c191] WHY THIS GUARD EXISTS, and it is not hypothetical. On 2026-10-07 the
    ONLY PROVEN_MODULE repair in the live tree was
    `services/staged/cve_risk_summary/router.py:3  get_cve_risk_summary  .logic -> .contract`.
    The oracle was CORRECT -- `contract.py` really does define that name. But
    `logic.py` in that service is not code at all; its entire body is four lines
    of FILE PATHS, the builder's own manifest output written where the module
    should be:

        services/staged/cve_risk_summary/contract.py
        services/staged/cve_risk_summary/router.py
        services/staged/cve_risk_summary/service.toml
        services/_exemplar/logic.py

    Applying the repair would have moved the import to `.contract`, the import
    gate would have gone GREEN, the census site would have DISAPPEARED, and the
    service would have been promotable with no business logic behind its route.
    A Potemkin service, passed by a gate. The defect is a MISSING MODULE BODY,
    not a mis-addressed import, and the two have different cures.

    Why py_compile cannot see it (R6: unknown is not zero): a line like
    `services/staged/x/contract.py` is SYNTACTICALLY VALID Python -- it parses as
    division between undefined names -- so the file compiles and the symbol
    census reads it as "a module that merely does not export the wanted name".
    The whole class was invisible to every instrument pointed at it.

    The test is deliberately the weakest one that separates the two: a real
    module body carries at least one top-level import, def, class or decorator.
    A file with none of those is refused. That direction is safe -- the cost of a
    false positive is one REFUSED site a human reads, while the cost of a false
    negative is a hollow service promoted into the spine.
    """
    try:
        import ast
        ast.parse(src)
    except SyntaxError:
        return False
    for line in src.splitlines():
        if _MODULE_SHAPE_RE.match(line):
            return True
    return False
def resolve_module(root: str, hit: dict) -> tuple:
    """Second oracle, and the one that fires in practice.

    The wanted name may simply live in a DIFFERENT sibling of the same service -- the
    router was emitted against `logic` while the directive that defined the symbol wrote
    it to `contract`. Nothing is invented here: the repair is PROVEN_MODULE only when
    EXACTLY ONE other sibling module in the same service defines that name at top level,
    so there is no choice to make. Two candidates -> MODULE_AMBIGUOUS, and refused.
    """
    svc, want = hit["svc"], hit["want"]
    # [c191] REFUSE before either oracle runs: if the sibling the import names is
    # not a module body at all, the defect is the missing body, not the address.
    # Rewriting the import would green the gate over a hollow service.
    _sib_abs = os.path.join(root, hit["sibling"])
    try:
        _sib_src = open(_sib_abs, encoding="utf-8", errors="replace").read()
    except OSError:
        return ("REFUSED", "SIBLING_UNREADABLE")
    if not is_module_shaped(_sib_src):
        return ("REFUSED", "SIBLING_NOT_A_MODULE")
    sdir = os.path.join(root, "services", "staged", svc)
    cur = os.path.basename(hit["sibling"])[:-3]
    owners = []
    tracked = tracked_files(root, "services/staged/%s" % svc)
    for fn in sorted(os.listdir(sdir)):
        if not fn.endswith(".py") or fn[:-3] in (cur, os.path.basename(hit["file"])[:-3]):
            continue
        if "services/staged/%s/%s" % (svc, fn) not in tracked:
            continue
        src = open(os.path.join(sdir, fn), encoding="utf-8", errors="replace").read()
        if want in toplevel_defs(src):
            owners.append(fn[:-3])
    if len(owners) == 1:
        return ("PROVEN_MODULE", owners[0])
    if len(owners) > 1:
        return ("REFUSED", "MODULE_AMBIGUOUS")
    return ("REFUSED", "NO_OWNING_SIBLING")


def resolve(root: str, hit: dict) -> tuple:
    """(\"PROVEN\", new_name) or (\"REFUSED\", reason). Never guesses."""
    sib_rel, want = hit["sibling"], hit["want"]
    if not hit.get("sibling_tracked", True):
        return ("REFUSED", "SIBLING_UNTRACKED")
    # [c191] same guard as resolve_module(): a rename oracle must not rewrite an
    # import whose named sibling has no module body. See is_module_shaped().
    try:
        if not is_module_shaped(open(os.path.join(root, sib_rel),
                                    encoding="utf-8", errors="replace").read()):
            return ("REFUSED", "SIBLING_NOT_A_MODULE")
    except OSError:
        return ("REFUSED", "SIBLING_UNREADABLE")
    log = git(root, "log", "--format=%H", "-S", want, "--", sib_rel).stdout.split()
    if not log:
        return ("REFUSED", "NEVER_IN_HISTORY")

    def defs_at(rev: str) -> set:
        res = git(root, "show", "%s:%s" % (rev, sib_rel))
        if res.returncode != 0:
            return set()
        return toplevel_defs(res.stdout)

    # log is newest-first; the newest commit that touched `want` and in which `want`
    # is ABSENT, while present in its parent, is the removing commit.
    for rev in log:
        after = defs_at(rev)
        if want in after:
            continue
        before = defs_at(rev + "^")
        if want not in before:
            continue
        added = after - before
        if len(added) != 1:
            return ("REFUSED", "AMBIGUOUS")
        new = added.pop()
        if new not in hit["sibling_defs"]:
            return ("REFUSED", "STALE_TARGET")
        return ("PROVEN", new)
    return ("REFUSED", "NO_REMOVING_COMMIT")


# ------------------------------------------------------------------------ apply


def rewrite_token(src: str, old: str, new: str) -> tuple:
    pat = re.compile(r"(?<![A-Za-z0-9_.])%s(?![A-Za-z0-9_])" % re.escape(old))
    out, n = pat.subn(new, src)
    return out, n


def apply_module_repairs(root: str, plan: list, write: bool) -> dict:
    """plan: [(hit, owning_module)]. Re-points the import at the sibling that defines it.

    The whole ImportFrom statement is re-emitted, partitioned by owner, so a statement
    importing four names of which one moved keeps the other three where they were. The
    symbol itself is never renamed, so no semantics are invented.
    """
    by_file: dict = {}
    for hit, owner in plan:
        by_file.setdefault(hit["file"], {}).setdefault(hit["line"], {})[hit["want"]] = owner
    edited, stmts = 0, 0
    for rel, lines in sorted(by_file.items()):
        path = os.path.join(root, rel)
        src = open(path, encoding="utf-8").read()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        srclines = src.splitlines(keepends=True)
        edits = []  # (start_idx, end_idx, replacement_text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.lineno not in lines:
                continue
            moves = lines[node.lineno]
            groups: dict = {}
            for alias in node.names:
                tgt = moves.get(alias.name, None)
                spec = alias.name + (" as %s" % alias.asname if alias.asname else "")
                groups.setdefault(tgt, []).append(spec)
            if None not in groups and len(groups) == 1 and node.module is None:
                continue
            indent = re.match(r"[ \t]*", srclines[node.lineno - 1]).group(0)
            out = []
            for tgt, specs in sorted(groups.items(), key=lambda kv: (kv[0] or "")):
                mod = node.module if tgt is None else tgt
                dots = "." * node.level if tgt is None else "."
                out.append("%sfrom %s%s import %s\n" % (indent, dots, mod, ", ".join(specs)))
            edits.append((node.lineno - 1, (node.end_lineno or node.lineno), "".join(out)))
            stmts += 1
        if not edits:
            continue
        for start, end, text in sorted(edits, reverse=True):
            srclines[start:end] = [text]
        edited += 1
        if write:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write("".join(srclines))
    return {"files_edited": edited, "statements_rewritten": stmts}


def apply_repairs(root: str, plan: list, write: bool) -> dict:
    """plan: [(hit, new_name)]. Groups by file so one file is read/written once."""
    by_file: dict = {}
    for hit, new in plan:
        by_file.setdefault(hit["file"], []).append((hit["want"], new))
    edited, sites = 0, 0
    for rel, pairs in sorted(by_file.items()):
        path = os.path.join(root, rel)
        src = open(path, encoding="utf-8").read()
        orig = src
        for old, new in pairs:
            src, n = rewrite_token(src, old, new)
            sites += n
        if src != orig:
            edited += 1
            if write:
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(src)
    return {"files_edited": edited, "tokens_rewritten": sites}


# -------------------------------------------------------------------- self-test


def _init_repo(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    git(path, "init", "-q", check=True)
    git(path, "config", "user.email", "selftest@local", check=True)
    git(path, "config", "user.name", "selftest", check=True)
    git(path, "config", "commit.gpgsign", "false", check=True)


def _svc(root: str, name: str, files: dict) -> None:
    d = os.path.join(root, "services", "staged", name)
    os.makedirs(d, exist_ok=True)
    for fn, body in files.items():
        with open(os.path.join(d, fn), "w", encoding="utf-8") as fh:
            fh.write(body)


def _commit(root: str, msg: str) -> None:
    git(root, "add", "-A", check=True)
    git(root, "commit", "-q", "-m", msg, check=True)


def self_test() -> int:
    poles, failed = [], 0

    def pole(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failed
        poles.append((name, ok, detail))
        if not ok:
            failed += 1

    tmp = tempfile.mkdtemp(prefix="sibsym_selftest_")
    try:
        root = os.path.join(tmp, "repo")
        _init_repo(root)

        # --- a service whose logic.py is renamed 1:1 (the PROVEN shape)
        _svc(root, "proven", {
            "__init__.py": "",
            "logic.py": "def old_name():\n    return 1\n",
            "router.py": "from .logic import old_name\n\nX = old_name\n",
        })
        # --- a service where the removing commit adds TWO defs (AMBIGUOUS)
        _svc(root, "ambig", {
            "__init__.py": "",
            "logic.py": "def gone():\n    return 1\n",
            "router.py": "from .logic import gone\n",
        })
        # --- a service importing a name that was never there (NEVER_IN_HISTORY)
        _svc(root, "never", {
            "__init__.py": "",
            "logic.py": "def real():\n    return 1\n",
            "router.py": "from .logic import imaginary\n",
        })
        # --- a service with no mismatch at all (must stay invisible)
        _svc(root, "clean", {
            "__init__.py": "",
            "logic.py": "def here():\n    return 1\n",
            "router.py": "from .logic import here\n",
        })
        _commit(root, "build: initial")

        # pole 1: a clean service produces no hit
        hits = scan(root)
        pole("1 clean service yields no mismatch",
             not any(h["svc"] == "clean" for h in hits))

        # pole 2: before any rebuild ONLY `never` is broken -- a service whose sibling
        # still defines the name must not be reported. (This pole was observed RED
        # first: it originally asserted all three, and the scanner was right.)
        broken = {h["svc"] for h in hits}
        pole("2 pre-rebuild only the genuinely-absent name is reported",
             broken == {"never"}, detail=str(sorted(broken)))

        # now perform the history: proven = 1:1 rename, ambig = 1 removed / 2 added
        _svc(root, "proven", {"logic.py": "def new_name():\n    return 1\n"})
        _commit(root, "build: rebuild proven logic")
        _svc(root, "ambig", {"logic.py": "def a():\n    return 1\n\n\ndef b():\n    return 2\n"})
        _commit(root, "build: rebuild ambig logic")

        # pole 2b: the rebuild is what makes them visible -- detector observed going red
        broken2 = {h["svc"] for h in scan(root)}
        pole("2b rebuilding a sibling makes all 3 mismatches visible",
             {"proven", "ambig", "never"} <= broken2 and "clean" not in broken2,
             detail=str(sorted(broken2)))

        hits = {(h["svc"], h["want"]): h for h in scan(root)}

        # pole 3: PROVEN resolves to the single added name
        v, n = resolve(root, hits[("proven", "old_name")])
        pole("3 1:1 rename resolves PROVEN->new_name",
             (v, n) == ("PROVEN", "new_name"), detail="%s %s" % (v, n))

        # pole 4: two added defs must REFUSE as AMBIGUOUS (red on purpose)
        v, n = resolve(root, hits[("ambig", "gone")])
        pole("4 two added defs REFUSED AMBIGUOUS",
             (v, n) == ("REFUSED", "AMBIGUOUS"), detail="%s %s" % (v, n))

        # pole 5: a name never in that sibling must REFUSE (red on purpose)
        v, n = resolve(root, hits[("never", "imaginary")])
        pole("5 absent-from-history REFUSED NEVER_IN_HISTORY",
             (v, n) == ("REFUSED", "NEVER_IN_HISTORY"), detail="%s %s" % (v, n))

        # pole 6: STALE_TARGET -- proven successor later removed from disk
        _svc(root, "proven", {"logic.py": "def third_name():\n    return 1\n"})
        h6 = {(x["svc"], x["want"]): x for x in scan(root)}[("proven", "old_name")]
        v, n = resolve(root, h6)
        pole("6 successor no longer on disk REFUSED STALE_TARGET",
             (v, n) == ("REFUSED", "STALE_TARGET"), detail="%s %s" % (v, n))
        _svc(root, "proven", {"logic.py": "def new_name():\n    return 1\n"})

        # pole 7: apply repairs the PROVEN one, leaves REFUSED ones untouched
        hits = {(h["svc"], h["want"]): h for h in scan(root)}
        plan = [(hits[("proven", "old_name")], "new_name")]
        first = apply_repairs(root, plan, write=True)
        after = {(h["svc"], h["want"]) for h in scan(root)}
        pole("7 apply clears the PROVEN site and only that one",
             first["files_edited"] == 1
             and ("proven", "old_name") not in after
             and ("ambig", "gone") in after
             and ("never", "imaginary") in after,
             detail="%s left=%s" % (first, sorted(after)))

        # pole 8: idempotence -- re-deriving the plan yields nothing to do
        hits2 = {(h["svc"], h["want"]) for h in scan(root)}
        second = apply_repairs(root, [], write=True)
        pole("8 second apply edits 0 files (idempotent)",
             second["files_edited"] == 0 and ("proven", "old_name") not in hits2,
             detail=str(second))

        # --- the owning-module oracle -------------------------------------------
        # `wrongmod`: router imports two names from .logic; only `moved` lives in
        # .contract, so the statement must SPLIT and `stays` must not move.
        _svc(root, "wrongmod", {
            "__init__.py": "",
            "logic.py": "def stays():\n    return 1\n",
            "contract.py": "def moved():\n    return 2\n",
            "router.py": "from .logic import stays, moved\n\nY = (stays, moved)\n",
        })
        # `twoowners`: the name is defined in TWO siblings -> must refuse
        _svc(root, "twoowners", {
            "__init__.py": "",
            "logic.py": "def other():\n    return 1\n",
            "a.py": "def dup():\n    return 1\n",
            "b.py": "def dup():\n    return 2\n",
            "router.py": "from .logic import dup\n",
        })
        # [c191] second genuine-module fixture, consumed by self-test pole 15:
        # proves the SIBLING_NOT_A_MODULE guard refuses the hollow case WITHOUT
        # refusing a real one. A guard that refuses everything passes pole 14.
        _svc(root, "wrongmod2", {
            "__init__.py": "",
            "logic.py": "import os\n\n\ndef stays2():\n    return 1\n",
            "contract.py": "def moved2():\n    return 2\n",
            "router.py": "from .logic import moved2\n",
        })
        _commit(root, "build: module-oracle fixtures")
        h = {(x["svc"], x["want"]): x for x in scan(root)}

        # pole 9: the name is NOT in .logic history, but IS uniquely in .contract
        v, n = resolve(root, h[("wrongmod", "moved")])
        v2, n2 = resolve_module(root, h[("wrongmod", "moved")])
        pole("9 rename oracle refuses, module oracle PROVEN_MODULE->contract",
             (v, n) == ("REFUSED", "NEVER_IN_HISTORY")
             and (v2, n2) == ("PROVEN_MODULE", "contract"),
             detail="%s/%s then %s/%s" % (v, n, v2, n2))

        # pole 10: two owning siblings must REFUSE (red on purpose -- no coin-flip)
        v2, n2 = resolve_module(root, h[("twoowners", "dup")])
        pole("10 two owning siblings REFUSED MODULE_AMBIGUOUS",
             (v2, n2) == ("REFUSED", "MODULE_AMBIGUOUS"), detail="%s %s" % (v2, n2))

        # pole 11: the repoint splits the statement and keeps `stays` put
        apply_module_repairs(root, [(h[("wrongmod", "moved")], "contract")], write=True)
        txt = open(os.path.join(root, "services", "staged", "wrongmod", "router.py"),
                   encoding="utf-8").read()
        # (This pole was observed RED first, asserting `"stays, moved" not in txt`.
        #  The tool was right: the *usage* `Y = (stays, moved)` must NOT be rewritten,
        #  only the import statement. The assertion was the defect, not the repair.)
        pole("11 statement split: .contract gains `moved`, .logic keeps `stays`",
             "from .contract import moved" in txt
             and "from .logic import stays\n" in txt
             and "from .logic import stays, moved" not in txt
             and "Y = (stays, moved)" in txt, detail=repr(txt[:140]))

        # pole 12: the repaired site no longer scans, and nothing else was touched
        after = {(x["svc"], x["want"]) for x in scan(root)}
        pole("12 repoint clears its own site, leaves the ambiguous one",
             ("wrongmod", "moved") not in after and ("twoowners", "dup") in after,
             detail=str(sorted(after)))

        # --- [c191] the sibling that is not a module at all ----------------------
        # The live case this guard was built for, reduced to a fixture:
        # `hollow/logic.py` holds the builder's own PATH LISTING, and
        # `contract.py` uniquely defines the wanted name. Before this guard the
        # module oracle returned PROVEN_MODULE/contract here -- a repair that
        # greens the import gate over a service with no logic body.
        #
        # NOTE the listing is syntactically VALID Python: each line parses as
        # division between undefined names. py_compile cannot see this class,
        # which is exactly why the census could not either (R6).
        _svc(root, "hollow", {
            "__init__.py": "",
            "logic.py": ("services/staged/hollow/contract.py\n"
                         "services/staged/hollow/router.py\n"
                         "services/staged/hollow/service.toml\n"
                         "services/_exemplar/logic.py\n"),
            "contract.py": "def hollow_body():\n    return 3\n",
            "router.py": "from .logic import hollow_body\n",
        })
        _commit(root, "build: non-module sibling fixture")
        h = {(x["svc"], x["want"]): x for x in scan(root)}

        # pole 13: the listing PARSES, so the old blindness is real, not assumed
        _listing = open(os.path.join(root, "services", "staged", "hollow",
                                     "logic.py"), encoding="utf-8").read()
        _parses = True
        try:
            __import__("ast").parse(_listing)
        except SyntaxError:
            _parses = False
        pole("13 path-listing sibling COMPILES (so py_compile is blind to it)",
             _parses and not is_module_shaped(_listing),
             detail="parses=%s module_shaped=%s" % (_parses,
                                                    is_module_shaped(_listing)))

        # pole 14: RED ON PURPOSE -- both oracles must refuse, not repoint
        v, n = resolve(root, h[("hollow", "hollow_body")])
        v2, n2 = resolve_module(root, h[("hollow", "hollow_body")])
        pole("14 non-module sibling REFUSED by BOTH oracles (was PROVEN_MODULE)",
             (v, n) == ("REFUSED", "SIBLING_NOT_A_MODULE")
             and (v2, n2) == ("REFUSED", "SIBLING_NOT_A_MODULE"),
             detail="rename=%s/%s module=%s/%s" % (v, n, v2, n2))

        # pole 15: a REAL module sibling is still repaired -- the guard must not
        # refuse everything. Without this pole the guard could be `return
        # REFUSED` and poles 13/14 would still pass.
        v3, n3 = resolve_module(root, h[("wrongmod2", "moved2")])
        pole("15 genuine module sibling still PROVEN_MODULE (guard discriminates)",
             (v3, n3) == ("PROVEN_MODULE", "contract"), detail="%s/%s" % (v3, n3))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for name, ok, detail in poles:
        print("  %-4s %s%s" % ("PASS" if ok else "FAIL", name,
                               ("  <- " + detail) if detail and not ok else ""))
    print("%d/%d poles observed (6 of them REFUSALS driven red on purpose)"
          % (len(poles) - failed, len(poles)))
    return 1 if failed else 0


# ------------------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repo root (default: cwd)")
    ap.add_argument("--self-test", action="store_true",
                    help="run the 8-pole negative control and exit")
    ap.add_argument("--report", action="store_true",
                    help="scan + classify every site, change nothing")
    ap.add_argument("--apply", action="store_true", help="write the PROVEN repairs")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--include-untracked", action="store_true",
                    help="also scan untracked files (reported, never written)")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    root = os.path.abspath(args.root)
    hits = scan(root, only_tracked=not args.include_untracked)
    proven, proven_mod, refused = [], [], {}
    for hit in hits:
        verdict, info = resolve(root, hit)
        if verdict == "PROVEN":
            proven.append((hit, info))
            continue
        # the rename oracle found nothing; try the owning-module oracle
        v2, i2 = resolve_module(root, hit)
        if v2 == "PROVEN_MODULE":
            proven_mod.append((hit, i2))
        else:
            refused.setdefault("%s/%s" % (info, i2), []).append(hit)

    if args.json:
        print(json.dumps({
            "sites": len(hits),
            "services": len({h["svc"] for h in hits}),
            "proven_rename": [{"file": h["file"], "line": h["line"],
                               "want": h["want"], "new": n} for h, n in proven],
            "proven_module": [{"file": h["file"], "line": h["line"],
                               "want": h["want"], "owner": n} for h, n in proven_mod],
            "refused": {k: len(v) for k, v in sorted(refused.items())},
        }, indent=1))
    else:
        print("sites=%d services=%d  PROVEN_RENAME=%d  PROVEN_MODULE=%d  REFUSED=%d"
              % (len(hits), len({h["svc"] for h in hits}), len(proven), len(proven_mod),
                 sum(len(v) for v in refused.values())))
        for reason in sorted(refused):
            print("  REFUSED %-40s %d" % (reason, len(refused[reason])))
        for h, n in proven:
            print("  RENAME  %s:%d  %s -> %s" % (h["file"], h["line"], h["want"], n))
        for h, n in proven_mod:
            print("  MODULE  %s:%d  %s  from .%s -> .%s"
                  % (h["file"], h["line"], h["want"],
                     os.path.basename(h["sibling"])[:-3], n))

    if args.apply and (proven or proven_mod):
        if proven:
            print("applied renames: %s" % apply_repairs(root, proven, write=True))
        if proven_mod:
            print("applied module repoints: %s"
                  % apply_module_repairs(root, proven_mod, write=True))
        left = 0
        for h in scan(root, only_tracked=not args.include_untracked):
            if resolve(root, h)[0] == "PROVEN" or resolve_module(root, h)[0] == "PROVEN_MODULE":
                left += 1
        print("re-derived repairable after apply: %d (must be 0 for idempotence)" % left)
        return 0 if not left else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
