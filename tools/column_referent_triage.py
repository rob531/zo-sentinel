#!/usr/bin/env python3
"""Classify referent_verify's MISSING COLUMN set by the LIVENESS of the file that names it.

WHY THIS EXISTS
---------------
`tools/referent_verify.py` reports a single number for columns -- on 2026-09-29,
`115 missing of 385 checked`. Issue #4080 asks whether to ARM that check. That
question cannot be answered from the count, because the count mixes two
populations that deserve opposite treatments:

  * a column named by a module that is MOUNTED IN THE SPINE or RUNNING ON THE
    HOST is a live product defect -- the query cannot return a row, and the
    surrounding helper almost always swallows the bus error into `[]`, so it
    reports EMPTY rather than BROKEN (HARNESS_DOCTRINE R6, unknown != zero);
  * a column named by an unmounted, unimported root module left behind by a
    build directive is sediment. Arming the check would block every PR on this
    repository for it. That is an outage, not enforcement.

"115" answers neither. This tool publishes the split, with its BASIS (R5).

IT IS NOT A GATE. It always exits 0 on a successful classification and it is
wired into no workflow. `referent_verify.py` remains the single judge of
referents, exactly as `improve_loop.py` remains the single selector.

HOW LIVENESS IS RESOLVED -- and the trap this tool was built around
------------------------------------------------------------------
R1 says resolve the running artifact from the RUNTIME, never from a repo path.
The first version of this classification (done by hand, 2026-09-29) matched a
running process to a repo file BY BASENAME and concluded that `pattern_learner`
was reachable from the live `signal_analyser`. It is not:

    RUNNING : /home/workspace/zo_sentinel/signal_analyser.py         20728 B, Sep 19
    IMPORTER: /home/workspace/zo_sentinel/sentinel/signal_analyser.py 1728 B, Jul 13

Two files, one basename, different md5s, and only the one that does NOT import
`pattern_learner` is running. That is FU-152's four-copies-three-decoys shape,
reproduced by the triage written to avoid it. So:

  * a runtime match is by FULL PATH SUFFIX, never by basename alone;
  * a spine match is by the literal `import_path` in `app/_spine_generated.py`;
  * transitive liveness requires an exact `import X` / `from X import` edge from
    a file that is itself MOUNTED or RUNTIME.

`--self-test` drives all of that RED on purpose before it is trusted (R4).
Control 1 is the basename decoy above: a classifier that scores it LIVE fails.

USAGE
    python tools/referent_verify.py --json rv.json
    python tools/column_referent_triage.py --report rv.json [--runtime-ps ps.txt]
    python tools/column_referent_triage.py --self-test

EXIT CODES
    0  classified (report-only, always 0 on success -- this is not a gate)
    2  UNKNOWN: the report could not be read or carried no columns section.
       NEVER 0 with "0 live", which would read as good news (R6).
    3  --self-test: a control did not discriminate.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LIVE_CLASSES = ("MOUNTED", "RUNTIME", "IMPORTED_BY_LIVE")


# ---------------------------------------------------------------- spine mounts
def spine_import_paths(root: Path) -> set[str]:
    """The literal import_path of every service the generated spine mounts.

    Parsed with ast, not imported: importing app._spine_generated pulls the
    whole app in and a broken service would make this tool's verdict depend on
    the app booting, which is not what it measures.
    """
    p = root / "app" / "_spine_generated.py"
    if not p.exists():
        return set()
    try:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "SPINE_MOUNTS" not in names:
                continue
            try:
                mounts = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                continue
            for m in mounts or []:
                if isinstance(m, dict) and m.get("import_path"):
                    out.add(str(m["import_path"]))
    return out


# -------------------------------------------------------------- runtime oracle
def runtime_paths(ps_text: str) -> list[str]:
    """Absolute .py paths found in a process listing, POSIX-normalised.

    Only absolute paths are kept. A bare `foo.py` in an argv gives no directory
    and therefore cannot resolve a file -- treating it as a match is the decoy
    bug in this module's docstring.
    """
    out = []
    for m in re.finditer(r"(/[A-Za-z0-9_.@+\-/]*\.py)", ps_text.replace("\\", "/")):
        out.append(m.group(1))
    return sorted(set(out))


def runtime_matches(relpath: str, rt: list[str]) -> list[str]:
    """Does `relpath` (repo-relative) match a running absolute path by SUFFIX?

    Suffix matching is on whole path segments: `sentinel/signal_analyser.py`
    does not match `/home/workspace/zo_sentinel/signal_analyser.py`, and
    `signal_analyser.py` matches it only because that IS the trailing segment
    set. A caller that needs to distinguish two same-named files must pass the
    relpath that carries its directory, which is what this tool does.
    """
    rel = relpath.strip("/").split("/")
    hits = []
    for abs_p in rt:
        seg = abs_p.strip("/").split("/")
        if len(seg) >= len(rel) and seg[-len(rel):] == rel:
            hits.append(abs_p)
    return hits


# ------------------------------------------------------------- import edges
def import_edges(root: Path, wanted: set[str]) -> dict[str, set[str]]:
    """module stem -> set of repo-relative files that import it exactly."""
    if not wanted:
        return {}
    alt = "|".join(re.escape(w) for w in sorted(wanted))
    pat = re.compile(
        r"(?:^|\n)[ \t]*(?:import[ \t]+(%s)\b"
        r"|from[ \t]+(%s)[ \t]+import\b"
        r"|importlib\.import_module\([ \t]*[\"'](%s)[\"'])" % (alt, alt, alt)
    )
    edges: dict[str, set[str]] = {w: set() for w in wanted}
    for p in root.rglob("*.py"):
        parts = p.parts
        if "__pycache__" in parts or ".git" in parts:
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = p.relative_to(root).as_posix()
        for m in pat.finditer(t):
            name = m.group(1) or m.group(2) or m.group(3)
            if rel != f"{name}.py":
                edges[name].add(rel)
    return edges


# ------------------------------------------------------------------ classify
def classify_file(rel: str, mounts: set[str], rt: list[str]) -> str:
    p = rel.replace("\\", "/")
    if runtime_matches(p, rt):
        return "RUNTIME"
    if p.startswith("quarantine/"):
        return "QUARANTINED"
    for prefix in ("services/staged/", "services/active/"):
        if p.startswith(prefix):
            svc = p.split("/")[2] if len(p.split("/")) > 2 else ""
            return "MOUNTED" if svc in mounts else "SEDIMENT"
    if p.startswith("services/"):
        svc = p.split("/")[1]
        return "MOUNTED" if svc in mounts else "SEDIMENT"
    if p.startswith("tests/"):
        return "TESTS"
    if p.startswith("tools/"):
        return "TOOLS"
    if p.startswith("app/"):
        return "MOUNTED"
    if "/" not in p:
        stem = p[:-3] if p.endswith(".py") else p
        return "MOUNTED" if stem in mounts else "SEDIMENT"
    return "SEDIMENT"


def triage(report: dict, root: Path, ps_text: str) -> dict:
    cols = report.get("columns") or {}
    missing = cols.get("missing")
    if not isinstance(missing, dict):
        raise ValueError("report has no columns.missing mapping")

    mounts = spine_import_paths(root)
    rt = runtime_paths(ps_text or "")

    sites: dict[str, set[str]] = {}
    for col, ss in missing.items():
        for s in ss or []:
            sites.setdefault(s.replace("\\", "/").rsplit(":", 1)[0], set()).add(col)

    first = {f: classify_file(f, mounts, rt) for f in sites}

    # transitive: a root module imported by something MOUNTED or RUNTIME is live
    root_stems = {Path(f).stem for f in sites if "/" not in f}
    edges = import_edges(root, root_stems)
    for f, c in list(first.items()):
        if c in LIVE_CLASSES or "/" in f:
            continue
        for importer in sorted(edges.get(Path(f).stem, ())):
            if classify_file(importer, mounts, rt) in ("MOUNTED", "RUNTIME"):
                first[f] = "IMPORTED_BY_LIVE"
                break

    live_cols: set[str] = set()
    for f, c in first.items():
        if c in LIVE_CLASSES:
            live_cols |= sites[f]

    by_class: dict[str, dict] = {}
    for f, c in sorted(first.items()):
        b = by_class.setdefault(c, {"files": [], "columns": set()})
        b["files"].append(f)
        b["columns"] |= sites[f]

    return {
        "basis": {
            "report_generated_at": report.get("generated_at"),
            "bus_captured_at": (report.get("catalog") or {}).get("bus_captured_at"),
            "bus_age_days": (report.get("catalog") or {}).get("bus_age_days"),
            "columns_checked": cols.get("checked"),
            "columns_missing": len(missing),
            "spine_mounts": len(mounts),
            "runtime_paths_seen": len(rt),
            "runtime_oracle": "process listing supplied" if rt else "NOT SUPPLIED -- runtime class is UNKNOWN, not empty",
        },
        "live_columns": sorted(live_cols),
        "live_column_count": len(live_cols),
        "sediment_column_count": len(missing) - len(live_cols),
        "by_class": {
            c: {"files": v["files"], "column_count": len(v["columns"]),
                "columns": sorted(v["columns"])}
            for c, v in sorted(by_class.items())
        },
        "file_class": first,
    }


def render(t: dict) -> str:
    b = t["basis"]
    L = []
    L.append("COLUMN REFERENT TRIAGE -- liveness of the file that names each missing column")
    L.append("")
    L.append(f"  BASIS  report {b['report_generated_at']}  bus snapshot {b['bus_captured_at']} "
             f"({b['bus_age_days']}d)")
    L.append(f"         {b['columns_missing']} missing of {b['columns_checked']} checked  "
             f"spine mounts {b['spine_mounts']}")
    L.append(f"         runtime oracle: {b['runtime_oracle']}")
    L.append("")
    L.append(f"  LIVE (mounted / running / imported by one) : {t['live_column_count']}")
    L.append(f"  SEDIMENT (unmounted, unimported)           : {t['sediment_column_count']}")
    L.append("")
    for c, v in t["by_class"].items():
        L.append(f"  {c:18s} files={len(v['files']):3d}  columns={v['column_count']:4d}")
    if t["live_columns"]:
        L.append("")
        L.append("  LIVE COLUMNS -- each of these is a query that cannot return a row:")
        for col in t["live_columns"]:
            L.append(f"      {col}")
        L.append("")
        L.append("  LIVE FILES:")
        for f, c in sorted(t["file_class"].items()):
            if c in LIVE_CLASSES:
                L.append(f"      [{c}] {f}")
    return "\n".join(L)


# ----------------------------------------------------------------- self-test
def _self_test() -> int:
    """Every control must be OBSERVED discriminating. R4: an assertion never
    seen red is an untested branch, so each control asserts BOTH poles."""
    ok = True

    def check(name, red_cond, green_cond):
        nonlocal ok
        good = red_cond and green_cond
        print(f"  [{'PASS' if good else 'FAIL'}] {name}"
              f"{'' if good else f'   (red_pole={red_cond} green_pole={green_cond})'}")
        if not good:
            ok = False

    ps = "python3 /home/workspace/zo_sentinel/signal_analyser.py\n"
    rt = runtime_paths(ps)

    # 1. THE BASENAME DECOY -- the bug this tool was written around.
    check(
        "1 basename decoy: sentinel/signal_analyser.py is NOT the running one",
        red_cond=(runtime_matches("sentinel/signal_analyser.py", rt) == []),
        green_cond=(runtime_matches("signal_analyser.py", rt) != []),
    )

    # 2. A bare relative path in argv resolves nothing.
    check(
        "2 bare argv name is not an absolute path",
        red_cond=(runtime_paths("python3 signal_analyser.py\n") == []),
        green_cond=(runtime_paths("python3 /a/b/signal_analyser.py\n") == ["/a/b/signal_analyser.py"]),
    )

    # 3. The spine parse is not a constant: a real mount is LIVE, a made-up one is not.
    mounts = spine_import_paths(ROOT)
    a_mount = sorted(mounts)[0] if mounts else None
    check(
        "3 spine mount classification discriminates",
        red_cond=(classify_file("definitely_not_a_service_zzz.py", mounts, []) == "SEDIMENT"),
        green_cond=(a_mount is not None
                    and classify_file(f"{a_mount}.py", mounts, []) == "MOUNTED"),
    )

    # 4. Segment-aligned suffix match: a partial segment must not match.
    rt2 = runtime_paths("python3 /srv/app/my_runner.py\n")
    check(
        "4 suffix match is segment-aligned, not substring",
        red_cond=(runtime_matches("runner.py", rt2) == []),
        green_cond=(runtime_matches("app/my_runner.py", rt2) != []),
    )

    # 5. An unreadable report is UNKNOWN (raises), never "0 live".
    raised = False
    try:
        triage({"columns": {}}, ROOT, "")
    except ValueError:
        raised = True
    good_shape = triage({"columns": {"missing": {}, "checked": 0}}, ROOT, "")
    check(
        "5 a report with no columns.missing is UNKNOWN, not 0-live",
        red_cond=raised,
        green_cond=(good_shape["live_column_count"] == 0
                    and good_shape["basis"]["columns_missing"] == 0),
    )

    # 6. Missing runtime oracle is reported as UNKNOWN, not as "nothing running".
    t_no = triage({"columns": {"missing": {}, "checked": 0}}, ROOT, "")
    t_yes = triage({"columns": {"missing": {}, "checked": 0}}, ROOT, ps)
    check(
        "6 absent runtime oracle is labelled UNKNOWN (R6)",
        red_cond=("NOT SUPPLIED" in t_no["basis"]["runtime_oracle"]),
        green_cond=("NOT SUPPLIED" not in t_yes["basis"]["runtime_oracle"]),
    )

    print()
    print("SELF-TEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 3


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", help="referent_verify.py --json output")
    ap.add_argument("--runtime-ps", help="file holding a process listing from the RUNTIME host")
    ap.add_argument("--out", help="write the triage as JSON here")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return _self_test()
    if not a.report:
        print("UNKNOWN: --report is required (or --self-test). This is 2, not a pass.",
              file=sys.stderr)
        return 2
    try:
        report = json.loads(Path(a.report).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"UNKNOWN: cannot read {a.report}: {e}. This is 2, not a pass.", file=sys.stderr)
        return 2

    ps_text = ""
    if a.runtime_ps:
        try:
            ps_text = Path(a.runtime_ps).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"UNKNOWN: cannot read {a.runtime_ps}: {e}. This is 2, not a pass.",
                  file=sys.stderr)
            return 2

    try:
        t = triage(report, ROOT, ps_text)
    except ValueError as e:
        print(f"UNKNOWN: {e}. This is 2, not a pass.", file=sys.stderr)
        return 2

    print(render(t))
    if a.out:
        Path(a.out).write_text(json.dumps(t, indent=1, default=list), encoding="utf-8")
        print(f"\ntriage -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
