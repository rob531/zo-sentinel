#!/usr/bin/env python3
"""e2e_builder_yield.py -- the REAL builder output funnel, LOUD about drop-offs.

THE VANITY-DONE ILLUSION THIS SURFACES:
The builder emits per-file "chain" directives that build a staged service as
`services/staged/<name>/{logic.py,router.py,contract.py,service.toml}`. A COMPLETE
staged service (all four files) is the only thing that can promote to
`services/active/<name>/` and get mounted in `app/_spine_generated.py` so a UI
consumer actually receives it (done-is-consumer-side, not producer-side).

The producer-side tally looks healthy: `directives/*.done.json` keeps climbing, so
"builds completed" reads green. But ~45% of staged services are INCOMPLETE -- a
directive marked .done emitted (say) only logic.py + contract.py, the router never
landed, and that service can NEVER promote. The .done count is a vanity metric: it
counts directives retired, not services DELIVERED. This tool measures the funnel the
.done count hides -- staged -> complete -> active -> served -- and prints a LOUD
`[E2E-YIELD-LOW]` banner when the staged->complete completion yield drops below a
floor, naming the single file concern missing from the most staged services.

Report-only + deterministic (regex over `_spine_generated.py`, no code execution --
see spine_mount_router.discover_mounts). Never crashes on absent dirs. The `--repo`
override is what makes the two-pole `--self-test` hermetic (fixture trees in tmp).

    python tools/e2e_builder_yield.py                 # human-readable funnel
    python tools/e2e_builder_yield.py --json          # metrics dict (lane consumption)
    python tools/e2e_builder_yield.py --floor 90      # fire the banner below 90% yield
    python tools/e2e_builder_yield.py --self-test     # two-pole merge gate (0/1)
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

DEFAULT_REPO = Path("/home/workspace/zo_sentinel")
DEFAULT_FLOOR = 80.0

# The four files a staged service MUST have before it can promote to active/ + mount.
_REQUIRED_FILES = ("logic.py", "router.py", "contract.py", "service.toml")
# The census of staged subdirs -- a short label per required file for the banner.
_CONCERN = {"logic.py": "logic", "router.py": "router",
            "contract.py": "contract", "service.toml": "toml"}


def _subdirs(d: Path) -> list[str]:
    """Immediate subdirectory names of `d` (sorted). [] if `d` is missing -- never crash.
    `__init__.py` and any other file is NOT a subdir, so it is skipped by design."""
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def _count_glob(d: Path, pattern: str) -> int:
    """Count files matching `pattern` directly under `d`. 0 if `d` is missing."""
    if not d.is_dir():
        return 0
    return sum(1 for _ in d.glob(pattern))


def discover_mounts(spine_file: Path) -> int:
    """Count UNIQUE `'name': '...'` entries in app/_spine_generated.py by regex -- no code
    execution (mirrors spine_mount_router.discover_mounts). 0 if the file is missing/empty."""
    try:
        text = spine_file.read_text(encoding="utf-8")
    except Exception:
        return 0
    return len(set(re.findall(r"'name':\s*'([^']+)'", text)))


def census(repo: Path = DEFAULT_REPO, floor: float = DEFAULT_FLOOR) -> dict:
    """Pure: compute the full builder output funnel for `repo`. No writes, no network.

    The staged->complete yield is the hinge: a staged service with all four required
    files is `complete` (promotable); anything less is dead weight the .done count hides."""
    repo = Path(repo)
    directives = repo / "directives"
    staged = repo / "services" / "staged"
    active = repo / "services" / "active"
    spine = repo / "app" / "_spine_generated.py"

    # 1-3: directive-side (the producer tally that reads green).
    directives_done = _count_glob(directives, "*.done.json")
    # pending/ excludes *.done.json / *.failed.json -- only truly-pending work.
    pending = directives / "pending"
    directives_pending = sum(
        1 for p in (pending.glob("*.json") if pending.is_dir() else [])
        if not (p.name.endswith(".done.json") or p.name.endswith(".failed.json"))
    )
    directives_parked = _count_glob(directives / "_parked_orphan_chain", "*.json")

    # 4-7: staged-side (the consumer-truth the funnel exposes).
    staged_dirs = _subdirs(staged)
    staged_total = len(staged_dirs)
    present = {f: 0 for f in _REQUIRED_FILES}
    staged_complete = 0
    for name in staged_dirs:
        sd = staged / name
        have = {f for f in _REQUIRED_FILES if (sd / f).is_file()}
        for f in have:
            present[f] += 1
        if len(have) == len(_REQUIRED_FILES):
            staged_complete += 1
    staged_incomplete = staged_total - staged_complete

    # 8-9: delivered-side.
    active_total = len(_subdirs(active))
    mounts_served = discover_mounts(spine)

    # 10: derived signals.
    completion_yield_pct = round(staged_complete / staged_total * 100, 2) if staged_total else 0.0
    # worst_missing: the required file ABSENT from the most staged services (a drop-off hotspot).
    # missing = staged_total - present[f]. Tie broken by _REQUIRED_FILES order (stable, deterministic).
    worst_file, worst_count = None, 0
    if staged_total:
        for f in _REQUIRED_FILES:
            missing = staged_total - present[f]
            if missing > worst_count:
                worst_file, worst_count = f, missing
    worst_missing_concern = _CONCERN[worst_file] if worst_file else None

    yield_low = bool(staged_total) and completion_yield_pct < floor

    return {
        "repo": str(repo),
        "floor": floor,
        "directives_done": directives_done,
        "directives_pending": directives_pending,
        "directives_parked": directives_parked,
        "staged_total": staged_total,
        "staged_with_logic": present["logic.py"],
        "staged_with_router": present["router.py"],
        "staged_with_contract": present["contract.py"],
        "staged_with_toml": present["service.toml"],
        "staged_complete": staged_complete,
        "staged_incomplete": staged_incomplete,
        "active_total": active_total,
        "mounts_served": mounts_served,
        "completion_yield_pct": completion_yield_pct,
        "worst_missing_concern": worst_missing_concern,
        "worst_missing_count": worst_count,
        "yield_low": yield_low,
    }


def _banner(m: dict) -> str:
    concern = m["worst_missing_concern"] or "none"
    return ("[E2E-YIELD-LOW] completion_yield=%s%% incomplete=%d worst_missing=%s(%d)"
            % (m["completion_yield_pct"], m["staged_incomplete"], concern, m["worst_missing_count"]))


def render(m: dict) -> str:
    """Human-readable funnel with the drop-off between each stage, plus the LOUD banner."""
    lines = []
    lines.append("=== E2E BUILDER-YIELD CENSUS (%s) ===" % m["repo"])
    lines.append("")
    lines.append("DIRECTIVE SIDE (producer tally -- reads green, hides the gap):")
    lines.append("  directives_done      : %d" % m["directives_done"])
    lines.append("  directives_pending   : %d" % m["directives_pending"])
    lines.append("  directives_parked    : %d" % m["directives_parked"])
    lines.append("")
    lines.append("STAGED -> COMPLETE (the hinge -- only COMPLETE services can promote):")
    lines.append("  staged_total         : %d" % m["staged_total"])
    lines.append("    with logic.py      : %d  (missing %d)" % (m["staged_with_logic"], m["staged_total"] - m["staged_with_logic"]))
    lines.append("    with router.py     : %d  (missing %d)" % (m["staged_with_router"], m["staged_total"] - m["staged_with_router"]))
    lines.append("    with contract.py   : %d  (missing %d)" % (m["staged_with_contract"], m["staged_total"] - m["staged_with_contract"]))
    lines.append("    with service.toml  : %d  (missing %d)" % (m["staged_with_toml"], m["staged_total"] - m["staged_with_toml"]))
    lines.append("  staged_complete      : %d" % m["staged_complete"])
    lines.append("  staged_incomplete    : %d   <-- DROP-OFF (built a directive, no deliverable service)" % m["staged_incomplete"])
    lines.append("  completion_yield     : %s%% (floor %s%%)" % (m["completion_yield_pct"], m["floor"]))
    lines.append("")
    lines.append("DELIVERED SIDE (consumer truth):")
    lines.append("  active_total         : %d   (promoted from complete staged)" % m["active_total"])
    lines.append("  mounts_served        : %d   (unique names in app/_spine_generated.py)" % m["mounts_served"])
    lines.append("")
    if m["yield_low"]:
        lines.append(_banner(m))
    else:
        lines.append("[E2E-YIELD-OK] completion_yield=%s%% >= floor %s%%" % (m["completion_yield_pct"], m["floor"]))
    return "\n".join(lines)


def _make_staged(staged: Path, name: str, files: tuple[str, ...]) -> None:
    sd = staged / name
    sd.mkdir(parents=True, exist_ok=True)
    for f in files:
        (sd / f).write_text("# fixture %s\n" % f, encoding="utf-8")


def _spine_with(names: tuple[str, ...]) -> str:
    body = ["# AUTO-GENERATED fixture spine", "SPINE_MOUNTS = ["]
    for n in names:
        body.append("    {'name': '%s', 'import_path': 'services.active.%s.router'}," % (n, n))
    body.append("]")
    return "\n".join(body) + "\n"


def self_test() -> int:
    """Two-pole, hermetic (fixture repos in tmp via --repo), discriminating (EXACT numbers).

    POLE A (healthy): 3 staged services x all 4 files, 2 active, 2 mounts -> 100% yield, no banner.
    POLE B (the defect): 4 staged -- 1 complete, 3 missing router (1 of those also missing logic)
                         -> 25% yield, worst_missing='router'(3), banner FIRES."""
    ok = True

    def ck(label: str, cond: bool) -> None:
        nonlocal ok
        print(("  [GREEN] " if cond else "  [RED]   ") + label)
        ok = ok and cond

    all4 = _REQUIRED_FILES

    with tempfile.TemporaryDirectory() as td:
        # ---- POLE A: healthy repo -> full yield, banner must NOT fire ----
        repo_a = Path(td) / "pole_a"
        staged_a = repo_a / "services" / "staged"
        _make_staged(staged_a, "alpha_api", all4)
        _make_staged(staged_a, "beta_api", all4)
        _make_staged(staged_a, "gamma_api", all4)
        # a stray non-dir in staged/ must be ignored (mirrors the live __init__.py)
        staged_a.joinpath("__init__.py").write_text("# pkg\n", encoding="utf-8")
        (repo_a / "services" / "active" / "alpha_api").mkdir(parents=True, exist_ok=True)
        (repo_a / "services" / "active" / "beta_api").mkdir(parents=True, exist_ok=True)
        (repo_a / "services" / "active").joinpath("__init__.py").write_text("# pkg\n", encoding="utf-8")
        app_a = repo_a / "app"
        app_a.mkdir(parents=True, exist_ok=True)
        app_a.joinpath("_spine_generated.py").write_text(_spine_with(("alpha_api", "beta_api")), encoding="utf-8")
        # directive side (vanity tally): 5 .done but only 3 complete services
        dirs_a = repo_a / "directives"
        dirs_a.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            dirs_a.joinpath("d%d.done.json" % i).write_text("{}", encoding="utf-8")

        a = census(repo_a, floor=80.0)
        print("=== POLE A (healthy -> 100%% yield, no banner) ===")
        ck("A: staged_total == 3", a["staged_total"] == 3)
        ck("A: staged_complete == 3", a["staged_complete"] == 3)
        ck("A: staged_incomplete == 0", a["staged_incomplete"] == 0)
        ck("A: completion_yield_pct == 100", a["completion_yield_pct"] == 100.0)
        ck("A: active_total == 2", a["active_total"] == 2)
        ck("A: mounts_served == 2", a["mounts_served"] == 2)
        ck("A: directives_done == 5 (vanity tally climbs)", a["directives_done"] == 5)
        ck("A: yield_low is False (banner does NOT fire)", a["yield_low"] is False)
        ck("A: [E2E-YIELD-LOW] banner NOT in render()", "[E2E-YIELD-LOW]" not in render(a))

        # ---- POLE B: the defect this tool exists to catch ----
        repo_b = Path(td) / "pole_b"
        staged_b = repo_b / "services" / "staged"
        _make_staged(staged_b, "complete_api", all4)                                   # complete
        _make_staged(staged_b, "no_router_1", ("logic.py", "contract.py", "service.toml"))   # missing router
        _make_staged(staged_b, "no_router_2", ("logic.py", "contract.py", "service.toml"))   # missing router
        _make_staged(staged_b, "no_router_no_logic", ("contract.py", "service.toml"))        # missing router AND logic
        # no services/active/ dir at all -> active_total must be 0, never crash
        app_b = repo_b / "app"
        app_b.mkdir(parents=True, exist_ok=True)
        app_b.joinpath("_spine_generated.py").write_text(_spine_with(("complete_api",)), encoding="utf-8")

        b = census(repo_b, floor=80.0)
        print("\n=== POLE B (defect -> 25%% yield, worst=router(3), banner FIRES) ===")
        ck("B: staged_total == 4", b["staged_total"] == 4)
        ck("B: staged_complete == 1", b["staged_complete"] == 1)
        ck("B: staged_incomplete == 3", b["staged_incomplete"] == 3)
        ck("B: staged_with_router == 1", b["staged_with_router"] == 1)
        ck("B: staged_with_logic == 3", b["staged_with_logic"] == 3)
        ck("B: worst_missing_concern == 'router'", b["worst_missing_concern"] == "router")
        ck("B: worst_missing_count == 3", b["worst_missing_count"] == 3)
        ck("B: completion_yield_pct == 25", b["completion_yield_pct"] == 25.0)
        ck("B: active_total == 0 (missing dir -> no crash)", b["active_total"] == 0)
        ck("B: mounts_served == 1", b["mounts_served"] == 1)
        ck("B: yield_low is True (banner fires)", b["yield_low"] is True)
        ck("B: [E2E-YIELD-LOW] banner IN render()", "[E2E-YIELD-LOW]" in render(b))
        ck("B: banner names router(3)", "worst_missing=router(3)" in _banner(b))

    print("\n=== SUMMARY ===")
    if ok:
        print("SELF-TEST PASS: Pole A 100%% yield / no-banner | Pole B 25%% yield / router(3) / banner fires")
        return 0
    print("SELF-TEST FAIL")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="E2E builder-yield census: staged->complete->active->served funnel")
    ap.add_argument("--repo", default=str(DEFAULT_REPO), help="repo root (override makes the self-test hermetic)")
    ap.add_argument("--floor", type=float, default=DEFAULT_FLOOR, help="completion-yield%% floor below which the LOUD banner fires")
    ap.add_argument("--json", action="store_true", help="emit the full metrics dict as JSON")
    ap.add_argument("--self-test", action="store_true", help="two-pole merge gate (returns 0/1)")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    m = census(Path(args.repo), floor=args.floor)
    if args.json:
        print(json.dumps(m, indent=2, sort_keys=True))
    else:
        print(render(m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
