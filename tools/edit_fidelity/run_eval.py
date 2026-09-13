#!/usr/bin/env python3
"""Orchestrate the edit-fidelity eval: select tasks, run repairers, emit results.

    python tools/edit_fidelity/run_eval.py --self-test
    python tools/edit_fidelity/run_eval.py --n-tasks 20 --arms oracle,sloppy,anthropic_plain,anthropic_preserve \
        --model <id> --key-cmd "python D:/agentvault/fetch_secret.py anthropic"

Exit codes (distinct on purpose -- "no task ran" must never read as "0 excess edits"):
    0  evaluated
    1  error
    2  SELF-TEST FAILED: the poles did not separate (oracle not 0.0/green, or sloppy not red)
    3  UNEVALUABLE: the validity gate produced no valid task
    4  BUDGET: call/cost cap reached mid-run (partial results are still written)

Every run writes results_<ts>.json and report_<ts>.md into --out-dir. The
task set is cached in --tasks-json so an A/B can be re-run on the identical
tasks without re-discovery; the gate's discard counts travel with it.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import json
import math
import os
import pathlib
import statistics
import subprocess
import sys
import time
from typing import Dict, List, Optional

_HERE = pathlib.Path(__file__).resolve()
if __package__ in (None, ""):
    sys.path.insert(0, str(_HERE.parents[2]))  # repo root -> `tools.edit_fidelity` importable

from tools.edit_fidelity import metrics as M  # noqa: E402
from tools.edit_fidelity import repairers as R  # noqa: E402
from tools.edit_fidelity import tasks as T  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_SELFTEST, EXIT_UNEVALUABLE, EXIT_BUDGET = 0, 1, 2, 3, 4
ARMS = ("oracle", "sloppy", "anthropic_plain", "anthropic_preserve")


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- scoring

def score_repair(repo: pathlib.Path, task: T.Task, output: str, timeout: int, python: Optional[str]) -> Dict:
    """Write the repair in place, run the target, restore, compute metrics."""
    with T.swapped_file(repo, task.source_file, task.reference, output) as path:
        run = M.pass_at_1(repo, task.test_target, touched=[path], timeout=timeout, python=python)
    row = {"passed": run["passed"], "rc": run["rc"], "n_passed": run["n_passed"], "test_secs": run["secs"]}
    try:
        row["fidelity_lev"] = round(M.fidelity_lev(task.reference, output), 6)
        row["lev"] = M.levenshtein(M.normalize_eol(task.reference), M.normalize_eol(output))
    except ValueError:
        row["fidelity_lev"] = None
    row["output"] = output          # raw, so anyone can re-score without re-spending
    try:
        row["delta_cc"] = M.delta_cc(task.reference, output)
        row["cc_reference"] = M.cognitive_complexity(task.reference)
    except SyntaxError:
        row["delta_cc"] = None          # output does not parse: UNKNOWN, not zero
        row["syntax_error"] = True
    row["diff"] = "".join(difflib.unified_diff(M.normalize_eol(task.reference).splitlines(True),
                                               M.normalize_eol(output).splitlines(True),
                                               "reference", "output", n=1))
    return row


def _score_one(repo: pathlib.Path, arm: str, repairer: R.Repairer, task: T.Task, i: int, n: int,
               timeout: int, python: Optional[str]) -> Dict:
    row = {"task": task.id, "arm": arm}
    t0 = time.time()
    try:
        res = repairer.repair(task)
    except R.BudgetExceeded:
        raise
    except Exception as e:  # noqa: BLE001 - a repair failure is a scored row, not a crash
        row.update({"error": f"{type(e).__name__}: {e}", "passed": False, "fidelity_lev": None, "delta_cc": None})
        log(f"  [{arm}] {i}/{n} {task.id}  ERROR {row['error'][:120]}")
        return row
    row.update(score_repair(repo, task, res.output, timeout, python))
    row["meta"] = res.meta
    row["secs"] = round(time.time() - t0, 2)
    log(f"  [{arm}] {i}/{n} {task.id}  pass={row['passed']} fid={row['fidelity_lev']} dcc={row['delta_cc']}")
    return row


def run_arms_interleaved(repo: pathlib.Path, repairers: Dict[str, R.Repairer], tasks: List[T.Task],
                         timeout: int, python: Optional[str]) -> Dict[str, List[Dict]]:
    """Task-major order: every task is scored by EVERY arm before the next task,
    so a budget cap that trips mid-run still leaves PAIRED rows. Rows collected
    before the cap are kept; the exception propagates after they are recorded."""
    rows: Dict[str, List[Dict]] = {a: [] for a in repairers}
    try:
        for i, task in enumerate(tasks, 1):
            for arm, rep in repairers.items():
                rows[arm].append(_score_one(repo, arm, rep, task, i, len(tasks), timeout, python))
    except R.BudgetExceeded as e:
        e.rows = rows  # type: ignore[attr-defined]
        raise
    return rows


def run_arm(repo: pathlib.Path, arm: str, repairer: R.Repairer, tasks: List[T.Task],
            timeout: int, python: Optional[str]) -> List[Dict]:
    return [_score_one(repo, arm, repairer, task, i, len(tasks), timeout, python)
            for i, task in enumerate(tasks, 1)]


def aggregate(rows: List[Dict]) -> Dict:
    scored = [r for r in rows if r.get("fidelity_lev") is not None]
    dcc = [r["delta_cc"] for r in rows if r.get("delta_cc") is not None]
    fid = [r["fidelity_lev"] for r in scored]
    n = len(rows)
    return {
        "n": n,
        "n_scored": len(scored),
        "n_error": sum(1 for r in rows if "error" in r),
        "n_unparseable": sum(1 for r in rows if r.get("syntax_error")),
        "fidelity_lev_mean": round(statistics.fmean(fid), 4) if fid else None,
        "fidelity_lev_median": round(statistics.median(fid), 4) if fid else None,
        "fidelity_lev_max": round(max(fid), 4) if fid else None,
        "delta_cc_mean": round(statistics.fmean(dcc), 3) if dcc else None,
        "delta_cc_positive": sum(1 for d in dcc if d > 0),
        "pass_at_1": round(sum(1 for r in rows if r.get("passed")) / n, 4) if n else None,
        "pass_count": sum(1 for r in rows if r.get("passed")),
    }


def sign_test(a: List[float], b: List[float]) -> Dict:
    """Paired two-sided sign test on (a_i < b_i). Ties dropped. stdlib only."""
    wins = sum(1 for x, y in zip(a, b) if x < y)
    losses = sum(1 for x, y in zip(a, b) if x > y)
    n = wins + losses
    if n == 0:
        return {"wins": wins, "losses": losses, "ties": len(a), "p_two_sided": None}
    k = min(wins, losses)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return {"wins": wins, "losses": losses, "ties": len(a) - n, "p_two_sided": round(min(1.0, 2 * p), 4)}


# --------------------------------------------------------------------------- self-test

def check_poles(oracle_rows: List[Dict], sloppy_rows: List[Dict]) -> Dict:
    """GREEN pole: every oracle row fidelity == 0.0 AND passed.
       RED pole:   every sloppy row passed AND fidelity > 0 AND delta_cc > 0."""
    o_ok = all(r.get("passed") and r.get("fidelity_lev") == 0.0 for r in oracle_rows) and bool(oracle_rows)
    s_ok = all(r.get("passed") and (r.get("fidelity_lev") or 0) > 0 and (r.get("delta_cc") or 0) > 0
               for r in sloppy_rows) and bool(sloppy_rows)
    return {
        "oracle_green": o_ok,
        "sloppy_red": s_ok,
        "separated": o_ok and s_ok,
        "oracle_observed": [(r["task"], r.get("fidelity_lev"), r.get("delta_cc"), r.get("passed")) for r in oracle_rows],
        "sloppy_observed": [(r["task"], r.get("fidelity_lev"), r.get("delta_cc"), r.get("passed")) for r in sloppy_rows],
    }


# --------------------------------------------------------------------------- report

def _fmt(v, nd=4):
    if v is None:
        return "UNKNOWN"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def write_report(path: pathlib.Path, payload: Dict) -> None:
    g = payload["gate"]
    lines = [f"# Edit-fidelity eval -- {payload['ts']}", ""]
    lines += [f"Repo `{payload['repo']}` @ `{payload['git_head']}`; seed {payload['seed']}; "
              f"model `{payload.get('model') or '-'}`.", ""]
    lines += ["## Validity gate (task discovery)", "",
              f"- pairs (source, test) considered: {g.get('pairs_considered')}",
              f"- candidates tried: {g.get('candidates_tried')}",
              f"- VALID tasks (test green on clean, RED on corrupted): {g.get('valid')}",
              f"- DISCARDED, tests stayed green (suite cannot see the mutation): {g.get('discarded_green')}",
              f"- discarded, timeout: {g.get('discarded_timeout')}",
              f"- pairs skipped, clean run not green / zero collected: {g.get('pairs_clean_fail')}",
              f"- pairs skipped, clean run over time budget: {g.get('pairs_clean_slow')}",
              f"- corrupt.py syntax rejects (must be 0): {g.get('syntax_rejects')}",
              f"- tried by class: {json.dumps(g.get('by_class_tried', {}), sort_keys=True)}",
              f"- valid by class: {json.dumps(g.get('by_class_valid', {}), sort_keys=True)}",
              f"- green(discarded) by class: {json.dumps(g.get('by_class_green', {}), sort_keys=True)}", ""]
    if payload.get("self_test"):
        st = payload["self_test"]
        lines += ["## Self-test poles", "",
                  f"- oracle GREEN pole (fidelity 0.0 and pass on every task): **{st['oracle_green']}**",
                  f"- sloppy RED pole (pass, fidelity > 0, delta_cc > 0 on every task): **{st['sloppy_red']}**",
                  f"- separated: **{st['separated']}**", ""]
    lines += ["## Results", "", "| arm | n | scored | errors | fidelity_lev mean | median | max | delta_cc mean | delta_cc>0 | pass@1 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for arm, agg in payload["aggregate"].items():
        lines.append(f"| {arm} | {agg['n']} | {agg['n_scored']} | {agg['n_error']} | {_fmt(agg['fidelity_lev_mean'])} | "
                     f"{_fmt(agg['fidelity_lev_median'])} | {_fmt(agg['fidelity_lev_max'])} | {_fmt(agg['delta_cc_mean'], 3)} | "
                     f"{agg['delta_cc_positive']} | {_fmt(agg['pass_at_1'])} ({agg['pass_count']}/{agg['n']}) |")
    lines.append("")
    if payload.get("paired"):
        p = payload["paired"]
        lines += ["## Paired comparison: anthropic_preserve vs anthropic_plain (same tasks)", "",
                  f"- n paired: {p['n']}",
                  f"- fidelity_lev: preserve lower on {p['fidelity']['wins']}, higher on {p['fidelity']['losses']}, "
                  f"ties {p['fidelity']['ties']}; sign-test p = {_fmt(p['fidelity']['p_two_sided'])}",
                  f"- delta_cc: preserve lower on {p['delta_cc']['wins']}, higher on {p['delta_cc']['losses']}, "
                  f"ties {p['delta_cc']['ties']}; sign-test p = {_fmt(p['delta_cc']['p_two_sided'])}",
                  f"- pass@1: preserve {p['pass_preserve']} vs plain {p['pass_plain']} (of {p['n']})", ""]
    if payload.get("usage"):
        lines += ["## Model usage", "", "```", json.dumps(payload["usage"], indent=1), "```", ""]
    if payload.get("exit_code") not in (None, 0):
        lines += [f"**EXIT {payload['exit_code']}** -- {payload.get('exit_reason', '')}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- main

def _git_head(repo: pathlib.Path) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(repo), capture_output=True,
                              text=True, timeout=30).stdout.strip() or "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


def _fetch_key(key_cmd: Optional[str]) -> Optional[str]:
    if not key_cmd:
        return os.environ.get("ANTHROPIC_API_KEY") or None
    p = subprocess.run(key_cmd, shell=True, capture_output=True, text=True, timeout=60)
    key = (p.stdout or "").strip().splitlines()
    return key[-1].strip() if key else None


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(_HERE.parents[2]))
    ap.add_argument("--seed", type=int, default=20260912)
    ap.add_argument("--n-tasks", type=int, default=20)
    ap.add_argument("--tasks-json", default=None, help="cache of selected tasks (default <out-dir>/tasks_<seed>.json)")
    ap.add_argument("--rediscover", action="store_true", help="ignore an existing --tasks-json")
    ap.add_argument("--arms", default="oracle,sloppy", help=f"comma list from {ARMS}")
    ap.add_argument("--self-test", action="store_true", help="prove both poles on live repo files; exit 2 if not separated")
    ap.add_argument("--self-test-n", type=int, default=3)
    ap.add_argument("--targets", default="auto", help="'auto' = the repo's evaluator.yml pytest list, or a file with one test path per line")
    ap.add_argument("--max-per-file", type=int, default=3)
    ap.add_argument("--max-candidates-per-file", type=int, default=12)
    ap.add_argument("--clean-budget-secs", type=float, default=30.0)
    ap.add_argument("--min-lines", type=int, default=T.DEFAULT_MIN_LINES)
    ap.add_argument("--max-bytes", type=int, default=T.DEFAULT_MAX_BYTES)
    ap.add_argument("--timeout", type=int, default=300, help="per pytest subprocess")
    ap.add_argument("--python", default=None, help="interpreter for pytest subprocesses (default: this one)")
    ap.add_argument("--model", default="claude-sonnet-4-5")
    ap.add_argument("--max-calls", type=int, default=25, help="per anthropic arm")
    ap.add_argument("--max-output-tokens", type=int, default=4096)
    ap.add_argument("--max-cost-usd", type=float, default=2.0, help="cap on cumulative estimated spend across both arms")
    ap.add_argument("--key-cmd", default=None, help="shell command whose stdout is the API key (never printed)")
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--out-dir", default=str(_HERE.parent / "results"))
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    repo = pathlib.Path(args.repo).resolve()
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    bad = [a for a in arms if a not in ARMS]
    if bad:
        log(f"unknown arm(s): {bad}; choose from {ARMS}")
        return EXIT_ERROR
    if args.self_test:
        arms = ["oracle", "sloppy"]
    recovered = T.recover_inflight(repo)
    if recovered:
        log(f"recovered {recovered}: a previous run was killed mid-swap; restored from the in-flight marker")

    if args.list_models:
        key = _fetch_key(args.key_cmd)
        if not key:
            log("no API key available")
            return EXIT_ERROR
        for m in R.list_models(key):
            log(m)
        return EXIT_OK

    # ---- tasks
    tasks_json = pathlib.Path(args.tasks_json) if args.tasks_json else out_dir / f"tasks_{args.seed}.json"
    n_target = args.self_test_n if args.self_test else args.n_tasks
    if tasks_json.is_file() and not args.rediscover:
        tasks, gate, meta = T.load_tasks(tasks_json)
        log(f"loaded {len(tasks)} cached tasks from {tasks_json} (gate: {gate.get('discarded_green')} discarded green)")
        # re-verify the cached references still match the live files
        stale = [t.id for t in tasks if T.read_text(repo / t.source_file) != t.reference]
        if stale:
            log(f"UNEVALUABLE: {len(stale)} cached task(s) no longer match the live file; use --rediscover: {stale[:3]}")
            return EXIT_UNEVALUABLE
        tasks = tasks[:n_target] if args.self_test else tasks
    else:
        if args.targets == "auto":
            targets = T.evaluator_targets(repo)
        else:
            targets = [ln.strip() for ln in pathlib.Path(args.targets).read_text(encoding="utf-8").splitlines() if ln.strip()]
        pairs = T.build_pairs(repo, targets, min_lines=args.min_lines, max_bytes=args.max_bytes)
        log(f"discovery: {len(targets)} test targets -> {len(pairs)} (source, test) pairs in the size window "
            f"[{args.min_lines}+ lines, <= {args.max_bytes} bytes]")
        t0 = time.time()
        tasks, gstats = T.select_tasks(repo, pairs, n_target=n_target, seed=args.seed,
                                       max_per_file=args.max_per_file,
                                       max_candidates_per_file=args.max_candidates_per_file,
                                       clean_budget_secs=args.clean_budget_secs, timeout=args.timeout,
                                       python=args.python, log=log)
        gate = gstats.to_dict()
        meta = {"seed": args.seed, "git_head": _git_head(repo), "targets": len(targets), "pairs": len(pairs),
                "discovery_secs": round(time.time() - t0, 1), "min_lines": args.min_lines, "max_bytes": args.max_bytes}
        T.save_tasks(tasks_json, tasks, gstats, meta)
        log(f"discovery done in {meta['discovery_secs']}s: {len(tasks)} valid, "
            f"{gate['discarded_green']} discarded green, {gate['candidates_tried']} tried; saved {tasks_json}")

    payload: Dict = {"ts": ts, "repo": str(repo), "git_head": _git_head(repo), "seed": args.seed,
                     "tasks_json": str(tasks_json), "gate": gate, "task_ids": [t.id for t in tasks],
                     "arms": arms, "model": args.model if any(a.startswith("anthropic") for a in arms) else None,
                     "rows": {}, "aggregate": {}}
    if not tasks:
        payload.update({"exit_code": EXIT_UNEVALUABLE, "exit_reason": "no valid task survived the gate"})
        _emit(out_dir, ts, payload)
        log("UNEVALUABLE: the validity gate produced no valid task. This is not '0 excess edits'.")
        return EXIT_UNEVALUABLE

    # ---- repairers
    key = None
    shared_usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    if any(a.startswith("anthropic") for a in arms):
        key = _fetch_key(args.key_cmd)
        if not key:
            log("no API key: set ANTHROPIC_API_KEY or pass --key-cmd")
            return EXIT_ERROR
        try:
            available = R.list_models(key)
        except Exception as e:  # noqa: BLE001
            log(f"could not list models ({type(e).__name__}); continuing with --model as given")
            available = None
        if available is not None and args.model not in available:
            log(f"model {args.model!r} is not in /v1/models for this key; available: {available}")
            return EXIT_ERROR
    repairers: Dict[str, R.Repairer] = {}
    for a in arms:
        if a == "oracle":
            repairers[a] = R.OracleRepairer()
        elif a == "sloppy":
            repairers[a] = R.SloppyRepairer()
        else:
            repairers[a] = R.AnthropicRepairer(preservation=(a == "anthropic_preserve"), model=args.model,
                                               api_key=key, max_output_tokens=args.max_output_tokens,
                                               max_calls=args.max_calls, max_cost_usd=args.max_cost_usd,
                                               shared_usage=shared_usage)
    del key

    # ---- run: deterministic arms one at a time; model arms interleaved per task
    exit_code, exit_reason = EXIT_OK, ""
    for a in [x for x in arms if not x.startswith("anthropic")]:
        log(f"== arm {a} over {len(tasks)} task(s)")
        payload["rows"][a] = run_arm(repo, a, repairers[a], tasks, args.timeout, args.python)
    model_arms = {a: repairers[a] for a in arms if a.startswith("anthropic")}
    if model_arms:
        log(f"== arms {list(model_arms)} interleaved over {len(tasks)} task(s), model {args.model}")
        try:
            payload["rows"].update(run_arms_interleaved(repo, model_arms, tasks, args.timeout, args.python))
        except R.BudgetExceeded as e:
            log(f"BUDGET: {e}")
            exit_code, exit_reason = EXIT_BUDGET, str(e)
            payload["rows"].update(getattr(e, "rows", {}))
    for a, rows in payload["rows"].items():
        payload["aggregate"][a] = aggregate(rows)
        log(f"  {a}: {json.dumps(payload['aggregate'][a])}")

    # ---- poles
    if "oracle" in payload["rows"] and "sloppy" in payload["rows"]:
        st = check_poles(payload["rows"]["oracle"], payload["rows"]["sloppy"])
        payload["self_test"] = st
        log(f"poles: oracle_green={st['oracle_green']} sloppy_red={st['sloppy_red']} separated={st['separated']}")
        for tag in ("oracle_observed", "sloppy_observed"):
            for tid, fid, dcc, ok in st[tag]:
                log(f"  {tag[:6]} {tid}: fidelity_lev={fid} delta_cc={dcc} pass={ok}")
        if args.self_test and not st["separated"]:
            exit_code, exit_reason = EXIT_SELFTEST, "self-test poles did not separate"

    # ---- paired A/B
    rp, rq = payload["rows"].get("anthropic_preserve"), payload["rows"].get("anthropic_plain")
    if rp and rq:
        pairs_ok = [(p, q) for p, q in zip(rp, rq)
                    if p.get("fidelity_lev") is not None and q.get("fidelity_lev") is not None]
        payload["paired"] = {
            "n": len(pairs_ok),
            "fidelity": sign_test([p["fidelity_lev"] for p, q in pairs_ok], [q["fidelity_lev"] for p, q in pairs_ok]),
            "delta_cc": sign_test([p.get("delta_cc") or 0 for p, q in pairs_ok],
                                  [q.get("delta_cc") or 0 for p, q in pairs_ok]),
            "pass_preserve": sum(1 for p, q in pairs_ok if p.get("passed")),
            "pass_plain": sum(1 for p, q in pairs_ok if q.get("passed")),
        }
        log(f"paired: {json.dumps(payload['paired'])}")

    usage = {a: r.usage_summary() for a, r in repairers.items() if a.startswith("anthropic")}
    if usage:
        tot = next(iter(usage.values()))  # the arms share one usage counter; report it once
        payload["usage"] = {"model": tot["model"], "calls_total": tot["calls"],
                            "input_tokens": tot["input_tokens"], "output_tokens": tot["output_tokens"],
                            "est_cost_usd": tot["est_cost_usd"], "pricing_known": tot["pricing_known"],
                            "calls_per_arm": {a: u["calls_this_arm"] for a, u in usage.items()}}
        log("usage: " + json.dumps(payload["usage"]))
    payload.update({"exit_code": exit_code, "exit_reason": exit_reason})
    _emit(out_dir, ts, payload)
    return exit_code


def _emit(out_dir: pathlib.Path, ts: str, payload: Dict) -> None:
    rj = out_dir / f"results_{ts}.json"
    rm = out_dir / f"report_{ts}.md"
    rj.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    write_report(rm, payload)
    log(f"wrote {rj}\nwrote {rm}")


if __name__ == "__main__":
    sys.exit(main())
