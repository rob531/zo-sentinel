#!/usr/bin/env python3
"""staged_yield_repair.py -- classify every INCOMPLETE staged service into repair
buckets and PLAN the directive re-emissions that would complete them. REPORT ONLY.

THE GENERATION HOLE THIS CLASSIFIES (root-caused -- do not re-derive):
The builder emits per-file directives building a staged service as
`services/staged/<name>/{logic.py,router.py,contract.py,service.toml}`. A COMPLETE
service (all four files) is the only thing that can promote to `services/active/`
and get mounted so a consumer actually receives it. Live: ~4689 staged, only ~1050
complete (22% yield). The collapse is a GENERATION hole -- ~1434 incomplete dirs have
`router.py` physically MISSING or emitted as non-code garbage (a bare path string /
prose note, not a FastAPI router with a bound `router = APIRouter(...)`).
`tools/service_decomposer.py` emits complete chains correctly; the failure is files
that never land or land HOLLOW.

`e2e_builder_yield.py` measures the yield and names the single worst-missing file.
This tool goes one level deeper: it CLASSIFIES each incomplete dir into exactly one
actionable bucket (so the largest fixable class is obvious) and, per dir, PLANS the
directive task-names that WOULD need re-emitting to complete it. It writes NOTHING --
no directive, no queue mutation, no apply. A plan, printed only.

Buckets (a dir is assigned to the FIRST matching bucket, in this order, so each
incomplete dir counts exactly once):
  code_complete_no_toml : has logic+router+contract, only service.toml missing
                          (deterministic re-emit -- service.toml is write_raw).
  router_missing        : router.py file absent entirely.
  router_hollow         : router.py present but has NO bound APIRouter (regex:
                          no `router = APIRouter` / no `APIRouter(` bound to router)
                          -- the non-code-garbage failure.
  logic_missing         : logic.py absent.
  toml_only             : ONLY service.toml present, no code files.
  dead_empty            : zero of the four files (or the dir is empty).
  other                 : any remaining incomplete combo.

Report-only + deterministic (regex over router.py contents, no code execution).
Never crashes on absent dirs. `--repo` makes the two-pole `--self-test` hermetic.

    python tools/staged_yield_repair.py                 # human bucket table + headline
    python tools/staged_yield_repair.py --json          # buckets + repair_plan_sample
    python tools/staged_yield_repair.py --self-test     # two-pole merge gate (0/1)
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

DEFAULT_REPO = Path("/home/workspace/zo_sentinel")

# The four files a staged service MUST have before it can promote to active/ + mount.
_REQUIRED_FILES = ("logic.py", "router.py", "contract.py", "service.toml")

# Buckets in ASSIGNMENT ORDER -- first match wins so each incomplete dir counts once.
_BUCKETS = (
    "code_complete_no_toml",
    "router_missing",
    "router_hollow",
    "logic_missing",
    "toml_only",
    "dead_empty",
    "other",
)

# A bound APIRouter -- either `router = APIRouter(...)` or `router=APIRouter` with any
# whitespace. A bare path string / prose note in router.py matches NEITHER (= hollow).
_BOUND_ROUTER_RE = re.compile(r"router\s*=\s*APIRouter\b")


def _subdirs(d: Path) -> list[str]:
    """Immediate subdirectory names of `d` (sorted). [] if `d` is missing -- never crash.
    A file (e.g. the live `__init__.py`) is NOT a subdir, so it is skipped by design."""
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def _router_is_hollow(router_path: Path) -> bool:
    """True when router.py exists but holds NO bound APIRouter -- the non-code-garbage
    failure (a bare path string / prose note instead of a FastAPI router). A real regex
    over file CONTENTS, not a stub. An unreadable file is treated as hollow."""
    try:
        text = router_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return True
    return _BOUND_ROUTER_RE.search(text) is None


def _would_reemit(service: str, missing_files: list[str], hollow_router: bool) -> list[str]:
    """The directive task-names that WOULD need re-emitting to complete `service`.
    PLAN ONLY -- this function writes nothing. A router that is present-but-hollow needs
    a rebuild too, so it is included even though the file technically exists on disk."""
    tasks: list[str] = []
    need = set(missing_files)
    if hollow_router:
        need.add("router.py")
    if "logic.py" in need:
        tasks.append("build_%s_logic" % service)
    if "router.py" in need:
        tasks.append("build_%s_router" % service)
    if "contract.py" in need:
        tasks.append("build_%s_contract" % service)
    if "service.toml" in need:
        tasks.append("scaffold_%s_service_toml" % service)
    return tasks


def _classify(have: set[str], hollow_router: bool) -> str:
    """Assign ONE bucket by the fixed first-match order. `have` is the set of the four
    required files present on disk; `hollow_router` is True only when router.py is present
    but carries no bound APIRouter. Caller guarantees the dir is INCOMPLETE."""
    has_logic = "logic.py" in have
    has_router = "router.py" in have
    has_contract = "contract.py" in have
    has_toml = "service.toml" in have
    n_have = len(have)

    # The two DEGENERATE states (near-empty dirs) are specific end-states, not a
    # one-file-short chain -- recognise them before the broad router/logic buckets,
    # otherwise a dir with no router (dead_empty / toml_only) would be swallowed by
    # `router_missing`. dead_empty is strictly more specific than toml_only, so it
    # cannot collide; both are checked up front.
    if n_have == 0:
        return "dead_empty"
    if have == {"service.toml"}:
        return "toml_only"

    # code_complete_no_toml: the three CODE files all present+real, only toml missing.
    if has_logic and has_router and not hollow_router and has_contract and not has_toml:
        return "code_complete_no_toml"
    if not has_router:
        return "router_missing"
    if hollow_router:          # router.py present but garbage
        return "router_hollow"
    if not has_logic:
        return "logic_missing"
    return "other"


def census(repo: Path = DEFAULT_REPO) -> dict:
    """Pure: classify every INCOMPLETE staged service in `repo`. No writes, no network.

    Returns bucket counts, the incomplete total, and a repair-plan sample (first 20
    incomplete dirs with their missing files + the directive tasks that would re-emit)."""
    repo = Path(repo)
    staged = repo / "services" / "staged"

    buckets = {b: 0 for b in _BUCKETS}
    plan: list[dict] = []
    incomplete_total = 0

    for name in _subdirs(staged):
        sd = staged / name
        have = {f for f in _REQUIRED_FILES if (sd / f).is_file()}
        # router.py present-but-hollow is a GENERATION defect even though the file exists.
        hollow_router = ("router.py" in have) and _router_is_hollow(sd / "router.py")

        complete = (len(have) == len(_REQUIRED_FILES)) and not hollow_router
        if complete:
            continue

        incomplete_total += 1
        bucket = _classify(have, hollow_router)
        buckets[bucket] += 1

        missing_files = [f for f in _REQUIRED_FILES if f not in have]
        plan.append({
            "service": name,
            "bucket": bucket,
            "missing_files": missing_files,
            "would_reemit": _would_reemit(name, missing_files, hollow_router),
        })

    # Headline = the LARGEST ACTIONABLE bucket. "other" is not an actionable repair class,
    # so it is excluded from the headline pick (tie broken by _BUCKETS order -- stable).
    headline_bucket, headline_count = None, 0
    for b in _BUCKETS:
        if b == "other":
            continue
        if buckets[b] > headline_count:
            headline_bucket, headline_count = b, buckets[b]
    headline_fires = headline_bucket is not None and headline_count > 0

    return {
        "repo": str(repo),
        "buckets": buckets,
        "incomplete_total": incomplete_total,
        "headline_bucket": headline_bucket,
        "headline_count": headline_count,
        "headline_fires": headline_fires,
        "repair_plan_sample": plan[:20],
    }


def _headline(m: dict) -> str:
    return ("[YIELD-REPAIR] largest_actionable_bucket=%s count=%d incomplete_total=%d"
            % (m["headline_bucket"], m["headline_count"], m["incomplete_total"]))


def render(m: dict) -> str:
    """Human-readable bucket table + the single [YIELD-REPAIR] headline."""
    lines = []
    lines.append("=== STAGED-YIELD REPAIR PLAN (%s) ===" % m["repo"])
    lines.append("")
    lines.append("INCOMPLETE staged services classified (first-match, each counts once):")
    for b in _BUCKETS:
        lines.append("  %-22s : %d" % (b, m["buckets"][b]))
    lines.append("  %-22s : %d" % ("INCOMPLETE TOTAL", m["incomplete_total"]))
    lines.append("")
    lines.append("REPORT + PLAN ONLY -- no directive written, no queue touched, no apply.")
    lines.append("")
    if m["headline_fires"]:
        lines.append(_headline(m))
    else:
        lines.append("[YIELD-REPAIR-OK] no incomplete staged services to repair")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Two-pole, hermetic, DISCRIMINATING self-test.
# --------------------------------------------------------------------------- #
def _make_staged(staged: Path, name: str, files_content: dict[str, str]) -> None:
    sd = staged / name
    sd.mkdir(parents=True, exist_ok=True)
    for f, content in files_content.items():
        (sd / f).write_text(content, encoding="utf-8")


_REAL_ROUTER = "from fastapi import APIRouter\nrouter = APIRouter()\n"
_GARBAGE_ROUTER = "services/staged/x/router.py\n"  # bare path string -- the hollow failure
_STUB = "# fixture\n"


def self_test() -> int:
    """Two-pole, hermetic (fixture repos in tmp via --repo), discriminating (EXACT counts).

    POLE A (healthy): 3 services x all 4 files, router.py holds a bound APIRouter ->
                      incomplete_total==0, every bucket 0, no [YIELD-REPAIR] headline.
    POLE B (defects): one service per bucket (code_complete_no_toml, router_missing,
                      router_hollow, logic_missing, toml_only, dead_empty) -> each
                      bucket==1, incomplete_total==6, re-emit plans correct, headline fires."""
    ok = True

    def ck(label: str, cond: bool) -> None:
        nonlocal ok
        print(("  [GREEN] " if cond else "  [RED]   ") + label)
        ok = ok and cond

    all4_real = {
        "logic.py": _STUB, "router.py": _REAL_ROUTER,
        "contract.py": _STUB, "service.toml": _STUB,
    }

    with tempfile.TemporaryDirectory() as td:
        # ---- POLE A: healthy -> nothing incomplete, no headline ----
        repo_a = Path(td) / "pole_a"
        staged_a = repo_a / "services" / "staged"
        _make_staged(staged_a, "alpha_api", all4_real)
        _make_staged(staged_a, "beta_api", all4_real)
        _make_staged(staged_a, "gamma_api", all4_real)
        # a stray non-dir in staged/ must be ignored (mirrors the live __init__.py)
        staged_a.joinpath("__init__.py").write_text("# pkg\n", encoding="utf-8")

        a = census(repo_a)
        print("=== POLE A (healthy -> 0 incomplete, no headline) ===")
        ck("A: incomplete_total == 0", a["incomplete_total"] == 0)
        ck("A: every bucket == 0", all(v == 0 for v in a["buckets"].values()))
        ck("A: headline_fires is False", a["headline_fires"] is False)
        ck("A: [YIELD-REPAIR] headline NOT in render()", "[YIELD-REPAIR]" not in render(a))

        # ---- POLE B: one service per bucket -- the defects this tool classifies ----
        repo_b = Path(td) / "pole_b"
        staged_b = repo_b / "services" / "staged"
        # code_complete_no_toml: logic+router(real)+contract, no toml
        _make_staged(staged_b, "ccnt_svc", {
            "logic.py": _STUB, "router.py": _REAL_ROUTER, "contract.py": _STUB})
        # router_missing: logic+contract+toml, router.py absent
        _make_staged(staged_b, "rmiss_svc", {
            "logic.py": _STUB, "contract.py": _STUB, "service.toml": _STUB})
        # router_hollow: all four present BUT router.py is garbage (a bare path string)
        _make_staged(staged_b, "rhollow_svc", {
            "logic.py": _STUB, "router.py": _GARBAGE_ROUTER,
            "contract.py": _STUB, "service.toml": _STUB})
        # logic_missing: router(real)+contract+toml, logic.py absent
        _make_staged(staged_b, "lmiss_svc", {
            "router.py": _REAL_ROUTER, "contract.py": _STUB, "service.toml": _STUB})
        # toml_only: ONLY service.toml
        _make_staged(staged_b, "tomlonly_svc", {"service.toml": _STUB})
        # dead_empty: zero of the four files (dir exists, empty-of-required)
        (staged_b / "dead_svc").mkdir(parents=True, exist_ok=True)

        b = census(repo_b)
        bk = b["buckets"]
        # Index the plan by service for exact re-emit assertions.
        plan_by = {p["service"]: p for p in b["repair_plan_sample"]}

        print("\n=== POLE B (one per bucket -> each==1, total==6, headline fires) ===")
        ck("B: incomplete_total == 6", b["incomplete_total"] == 6)
        ck("B: code_complete_no_toml == 1", bk["code_complete_no_toml"] == 1)
        ck("B: router_missing == 1", bk["router_missing"] == 1)
        ck("B: router_hollow == 1", bk["router_hollow"] == 1)
        ck("B: logic_missing == 1", bk["logic_missing"] == 1)
        ck("B: toml_only == 1", bk["toml_only"] == 1)
        ck("B: dead_empty == 1", bk["dead_empty"] == 1)
        ck("B: other == 0", bk["other"] == 0)
        # discriminating: the hollow router is NOT miscounted as complete or as code_complete_no_toml
        ck("B: rhollow_svc bucket == 'router_hollow'",
           plan_by.get("rhollow_svc", {}).get("bucket") == "router_hollow")
        # re-emit plan correctness
        ck("B: ccnt_svc would_reemit == ['scaffold_ccnt_svc_service_toml']",
           plan_by.get("ccnt_svc", {}).get("would_reemit") == ["scaffold_ccnt_svc_service_toml"])
        ck("B: rmiss_svc would_reemit == ['build_rmiss_svc_router']",
           plan_by.get("rmiss_svc", {}).get("would_reemit") == ["build_rmiss_svc_router"])
        # hollow router has the file on disk, so missing_files is empty, yet it must still re-emit router
        ck("B: rhollow_svc would_reemit includes build_rhollow_svc_router",
           "build_rhollow_svc_router" in plan_by.get("rhollow_svc", {}).get("would_reemit", []))
        ck("B: rmiss_svc missing_files == ['router.py']",
           plan_by.get("rmiss_svc", {}).get("missing_files") == ["router.py"])
        ck("B: headline_fires is True", b["headline_fires"] is True)
        ck("B: [YIELD-REPAIR] headline IN render()", "[YIELD-REPAIR]" in render(b))
        # six buckets tie at 1; first-match order picks code_complete_no_toml for the headline
        ck("B: headline_bucket == 'code_complete_no_toml' (tie -> first actionable)",
           b["headline_bucket"] == "code_complete_no_toml")

        # ---- POLE B extra: robust to a totally absent staged/ dir (never crash) ----
        repo_c = Path(td) / "pole_c_missing"
        c = census(repo_c)
        ck("C: absent repo -> incomplete_total == 0, no crash", c["incomplete_total"] == 0)
        ck("C: absent repo -> headline_fires False", c["headline_fires"] is False)

    print("\n=== SUMMARY ===")
    if ok:
        print("SELF-TEST PASS: Pole A 0-incomplete/no-headline | "
              "Pole B 6 buckets each=1 / re-emit plans correct / headline fires")
        return 0
    print("SELF-TEST FAIL")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Staged-yield repair planner: classify incomplete staged services + plan re-emits (report-only)")
    ap.add_argument("--repo", default=str(DEFAULT_REPO),
                    help="repo root (override makes the self-test hermetic)")
    ap.add_argument("--json", action="store_true",
                    help="emit buckets + incomplete_total + repair_plan_sample as JSON")
    ap.add_argument("--self-test", action="store_true", help="two-pole merge gate (returns 0/1)")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    m = census(Path(args.repo))
    if args.json:
        out = {
            "buckets": m["buckets"],
            "incomplete_total": m["incomplete_total"],
            "repair_plan_sample": m["repair_plan_sample"],
        }
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(render(m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
