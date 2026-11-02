# weekly_rescore_lib/complete.py -- PART OF weekly_rescore.py, not a standalone module.
# import, backfill, postcheck phases, and main entrypoint.
# weekly_rescore.py runs this file via _weekly_rescore_lib_exec("complete") INSIDE
# weekly_rescore's own namespace, at the exact position this code stood
# (original lines 1508-1979), so every name here is still
# weekly_rescore.<name> and __file__ is weekly_rescore.py.
# Do not import it directly; callers keep citing weekly_rescore.<name>.
# (chain rebase_godfiles S20, 2026-11-02; map: weekly_rescore_lib/README.md)
from __future__ import annotations


def ph_import(run: Run, args) -> None:
    if run.done("import") or run.state["phases"].get("import") == "skipped":
        return
    from psycopg2.extras import execute_values
    preds_gz = run.dir / "results" / "preds.jsonl.gz"
    if not preds_gz.exists():
        raise SystemExit("ALERT: preds.jsonl.gz missing at import; aborting (no writes)")
    # FU-094: VALIDITY GATE. Row counts and degraded=false are proxies; they
    # let 3 weeks of base+random-head noise into the moat. Judge the OUTPUT:
    # a real classifier cannot emit one label ~100% of the time. Fail CLOSED.
    #
    # FU-108: this block used to re-implement label extraction and read
    # record[axis], but the preds shape nests labels under `axis_pred_label`.
    # It handed the gate 0 rows on 20,576 VALID records and aborted the run --
    # the gate condemned data it never saw. Extraction now lives in ONE place,
    # score_validity.extract_axis_rows, which the writer loop below also uses,
    # so gate and writer can no longer read different shapes.
    import gzip as _gz, json as _json
    sys.path.insert(0, str(HERE))
    from score_validity import (assert_importable, extract_axis_rows,
                                format_report, ExtractionFailure)
    from calibration import (LADDERS, apply_calibration, calibration_enabled,
                             escalation_gate, rule_version)
    _recs = []
    with _gz.open(preds_gz, "rt", encoding="utf-8") as _fh:
        for _line in _fh:
            _line = _line.strip()
            if not _line:
                continue
            try:
                _recs.append(_json.loads(_line))
            except Exception:
                continue
    _rows = extract_axis_rows(_recs, AXES)
    _parsed = sum(1 for _d in _recs if _d.get("status") == "parsed")
    log("validity gate input: {} records ({} parsed) -> {} gradeable rows".format(
        len(_recs), _parsed, len(_rows)))
    try:
        _v = assert_importable(_rows, source_records=len(_recs))
    except ExtractionFailure as _e:
        # A shape/caller defect is NOT a verdict on the scores. Say so loudly
        # and do not let anyone read this as "the run produced garbage".
        raise SystemExit(
            "ALERT: validity gate could not READ the preds (caller/shape "
            "defect, not bad scores) -- {} records, {} parsed, 0 gradeable. "
            "The run artifacts are intact and re-importable once fixed; do NOT "
            "re-fire a paid job. {}".format(len(_recs), _parsed, _e))
    log("validity gate PASS: " + format_report(_v))
    for _a in _v["axes"]:
        if _a["verdict"] == "VALID_DECLARED":
            log("validity gate WARN: {} passed under a DECLARED exception -- {}"
                .format(_a["axis"], _a.get("reason")))
    _gate_rows = len(_rows)
    scored_at = datetime.utcnow()
    conn = pg_conn(); cur = conn.cursor()
    capture = os.environ.get("RESCORE_CAPTURE_DELTAS", "1") != "0"   # kill switch
    delta_stats = {}          # axis -> {"new": n, "changed": n, "unchanged": n}

    # The escalation rule now lives in calibration.escalation_gate -- same
    # 0.40/0.30 cutoffs, extracted verbatim so it can be tested without a DB.
    # The -1 severity remap is DEFAULT OFF (ZO_CALIBRATION_V2); with the flag
    # unset apply_calibration is a strict no-op and RULE stays v1.
    _calib_on = calibration_enabled()
    _calib_rule = rule_version(_calib_on)
    log("calibration remap: {} (decision_rule_version={})".format(
        "ON" if _calib_on else "OFF (default)", _calib_rule))

    rows, sids, servers, seen = [], [], 0, set()
    written = 0                      # FU-108: counted against the gate's view

    def flush():
        nonlocal rows, sids, capture
        if not rows:
            return
        prev = {}
        if capture:
            try:
                cur.execute("select server_id, axis_name, label, label_index, p_top, escalated, scored_at "
                            "from mcp_llm_axis_scores where model_version=%s and server_id=any(%s)",
                            (MODEL_VERSION, sids))
                prev = {(r[0], r[1]): r[2:] for r in cur.fetchall()}
            except Exception as e:
                conn.rollback(); capture = False
                log(f"delta-capture OFF (prev-read failed): {e}")
        events = []
        if capture:
            for r in rows:
                sid_r, axis, lbl, lidx, ptop_new, esc_new = r[0], r[1], r[2], r[3], r[5], r[8]
                st = delta_stats.setdefault(axis, {"new": 0, "changed": 0, "unchanged": 0})
                old = prev.get((sid_r, axis))
                if old is None:
                    st["new"] += 1
                elif old[1] != lidx or bool(old[3]) != bool(esc_new):
                    st["changed"] += 1
                    events.append((sid_r, axis, MODEL_VERSION, run.state["run_id"],
                                   old[0], old[1], old[2], old[4], lbl, lidx, ptop_new))
                else:
                    st["unchanged"] += 1

        def _apply(evs):
            cur.execute("delete from mcp_llm_axis_scores where model_version=%s and server_id=any(%s)",
                        (MODEL_VERSION, sids))
            execute_values(cur,
                "insert into mcp_llm_axis_scores (server_id, axis_name, label, label_index, probs, "
                "p_top, p_critical, p_danger, escalated, escalated_to, decision_rule_version, "
                "model_version, adapter_sha256, scored_at) values %s", rows)
            if evs:
                execute_values(cur,
                    "insert into score_change_events (server_id, axis_name, model_version, run_id, "
                    "prev_label, prev_label_index, prev_p_top, prev_scored_at, new_label, "
                    "new_label_index, new_p_top) values %s", evs)
            conn.commit()

        try:
            _apply(events if capture else None)
        except Exception as e:
            conn.rollback()
            if capture:               # analytics must never break scoring: retry bare
                capture = False
                log(f"delta-capture OFF (events insert failed): {e}; batch retried without events")
                _apply(None)
            else:
                raise
        rows, sids = [], []

    with gzip.open(preds_gz, "rt", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            p = json.loads(ln)
            if p.get("status") != "parsed":
                continue
            sid = p.get("server_id") or p.get("metadata", {}).get("server_id")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            pl, pi = p.get("axis_pred_label", {}), p.get("axis_pred_int", {})
            mp, pr = p.get("axis_max_prob", {}), p.get("axis_probs", {})
            esc, esc_to, pc = escalation_gate(pr.get("overall_risk", [0, 0, 0, 0]))
            _pre_len = len(rows)
            for a in AXES:
                if pi.get(a) == -1:
                    continue
                pv = pr.get(a, [])
                pdg = (pv[4] if a == "maintainer_trust" and len(pv) > 4 else
                       pv[3] if a == "network_egress" and len(pv) > 3 else None)
                _li, _lb, _pt = apply_calibration(a, pi.get(a), pv, LADDERS.get(a, ()),
                                                  _calib_on, mp.get(a), pl.get(a))
                rows.append((sid, a, _lb, _li, json.dumps(pv), _pt,
                             pc if a == "overall_risk" else None, pdg,
                             esc if a == "overall_risk" else False,
                             esc_to if a == "overall_risk" else None,
                             _calib_rule, MODEL_VERSION, ADAPTER_SHA_PIN, scored_at))
            sids.append(sid)
            servers += 1
            written += len(rows) - _pre_len
            if len(rows) >= 3500:
                flush()
    flush()
    # FU-108 differential: the gate must have judged exactly what we wrote.
    # A mismatch means gate and writer disagree about the preds shape again --
    # fail loudly rather than let a partially-judged import land.
    if written != _gate_rows:
        raise SystemExit(
            "ALERT: GATE/WRITER SHAPE DRIFT -- gate judged {} rows but writer "
            "produced {}. Import already committed in batches; investigate "
            "before trusting these scores (FU-108).".format(_gate_rows, written))
    cur.execute("select count(distinct server_id) from mcp_llm_axis_scores where model_version=%s",
                (MODEL_VERSION,))
    scored_after = cur.fetchone()[0]
    if capture and delta_stats:
        try:
            execute_values(cur,
                "insert into score_change_runs (run_id, model_version, axis_name, n_new, n_changed, "
                "n_unchanged) values %s on conflict on constraint uq_scr_run_axis do update set "
                "n_new=excluded.n_new, n_changed=excluded.n_changed, n_unchanged=excluded.n_unchanged",
                [(run.state["run_id"], MODEL_VERSION, a, s["new"], s["changed"], s["unchanged"])
                 for a, s in sorted(delta_stats.items())])
            conn.commit()
            log("delta-capture: " + json.dumps(delta_stats, sort_keys=True))
        except Exception as e:
            conn.rollback()
            log(f"delta-capture aggregates skipped: {e}")
    conn.close()
    before = run.state.get("scored_before", 0)
    if scored_after < before:                              # I1
        raise SystemExit(f"ALERT: NO-LOSS INVARIANT BREACH scored_after={scored_after} "
                         f"< before={before} -- investigate immediately")
    coverage = servers / max(1, run.state.get("exported", 1))
    run.mark("import", imported_servers=servers, scored_after=scored_after,
             coverage=round(coverage, 3),
             degraded=coverage < 0.90,                     # I2
             delta_summary=delta_stats or None)
    log(f"import OK: servers={servers} coverage={coverage:.1%} scored_after={scored_after}")


def ph_backfill(run: Run, args) -> None:
    if run.done("backfill") or run.state["phases"].get("backfill") == "skipped":
        return
    # ADVISORY-WAVE GUARD (2026-10-15). apply_risk_tier_backfill stamps the SINGLE
    # mcp_server_registry.risk_tier column (not versioned) AND un-asserts any server
    # lacking rows under the given model_version. For a coexisting advisory version
    # (e.g. v3.4 alongside v3.0) that would OVERWRITE the v3.0-derived registry tier
    # the prod verdict reads and un-assert the whole corpus mid-rescore. So when
    # RESCORE_SKIP_BACKFILL=1 the advisory wave writes ONLY mcp_llm_axis_scores and
    # leaves the registry tier (the verdict-read path) untouched. The scheduled v3.0
    # lane does not set this and backfills as before. REVERT: unset the env.
    if os.environ.get("RESCORE_SKIP_BACKFILL") == "1":
        run.mark("backfill", "skipped")
        log(f"backfill SKIPPED (RESCORE_SKIP_BACKFILL=1): advisory wave "
            f"model_version={MODEL_VERSION} writes axis scores only; "
            f"mcp_server_registry.risk_tier (verdict-read path) left untouched")
        return
    ensure_proxy()
    r = subprocess.run([sys.executable, str(HERE / "apply_risk_tier_backfill.py"),
                        "--dsn-file", str(DSN_FILE), "--apply",
                        "--model-version", MODEL_VERSION],
                       capture_output=True, text=True, timeout=7200)   # 1800s cap killed backfill mid-UPDATE at 232K-registry scale (2026-07-18)
    (run.dir / "backfill.log").write_text(r.stdout[-5000:] + "\n" + r.stderr[-2000:])
    if r.returncode != 0:
        raise SystemExit(f"ALERT: risk-tier backfill failed rc={r.returncode}; "
                         f"scores ARE imported; rerun backfill phase after fixing")
    run.mark("backfill")
    log("backfill OK")


ZERO_YIELD_RATE = 0.001            # 0.1% of the refresh half


def refresh_yield(state: dict) -> dict | None:
    """What did the REFRESH half of this delta cohort actually change?

    Measured 2026-09-01 across every landed delta wave's own `delta_summary`,
    the `overall_risk` axis:

        20260726-014732    15,236 / 20,000    76.2 %
        20260727-024623    97,989 / 120,000   81.6 %
        20260727-105859   136,116 / 140,000   97.2 %
        20260730-001738         0 / 20,000     0.0 %   <-- regime change
        20260804-060703         0 / 20,000     0.0 %
        20260831-033413         1 / 20,000     0.005 %

    Three consecutive landed waves spent 60,000 server-slots -- the majority of
    every delta cohort -- to move ONE server on ONE axis. Every one of those
    numbers was already being written, to `state.json` and to the
    `score_change_runs` table, and nothing ever read one back. So the phase was
    dead for four weeks and about a dollar of GPU time, and the run reports that
    a successor opens said "ok" each time, truthfully and uselessly.

    THIS IS A REPORT, NOT A GATE. It cannot abort a run, change an exit code or
    veto a wave (HARNESS_DOCTRINE R7: prefer RECOVERY over RESTRICTION; and the
    standing rule against answering a finding with another required check). What
    to DO about a dead refresh half -- shrink `--refresh-cap`, move the budget to
    never-scored servers -- is cohort policy, and cohort policy goes through peer
    review, not through a quiet edit in a reporting function.

    Returns None when there is no refresh half to describe, so the loud line
    means exactly one thing wherever it appears.

    UNKNOWN IS NOT ZERO (R6). `unmeasured` and `zero_yield` are opposite facts
    that look identical if you collapse a missing summary to 0: one says the
    phase did nothing, the other says we never asked. A run whose import died
    before writing the aggregates gets `unmeasured`, and a decision made on it
    is a decision made on nothing, which is the point of saying so.
    """
    refresh = state.get("refresh_servers") or 0
    if refresh <= 0:
        return None

    summary = state.get("delta_summary") or {}
    axes = [a for a in summary.values() if isinstance(a, dict) and "changed" in a]
    if not axes:
        # R6: no measurement is not a measurement of nothing.
        return {"refresh_servers": refresh, "changed": None, "rate": None,
                "verdict": "unmeasured",
                "basis": "delta_summary absent or empty -- the refresh half was "
                         "NOT measured this run; this is not a reading of zero"}

    # Seven axes ship. A wave that moved only `auth_strength` is productive, and
    # keying this on `overall_risk` alone would bury it.
    changed = max(int(a.get("changed") or 0) for a in axes)
    rate = changed / refresh
    return {
        "refresh_servers": refresh,
        "changed": changed,
        "rate": rate,
        "verdict": "zero_yield" if rate < ZERO_YIELD_RATE else "productive",
        "basis": f"max(changed) over {len(axes)} axis/axes of "
                 f"delta_summary, / refresh_servers",
    }



def zero_yield_streak(runs_root=None) -> dict:
    """How many CONSECUTIVE landed waves ended with a dead refresh half?

    Derived, never restated. Until 2026-09-22 the postcheck carried this as
    literal prose -- "Three landed waves in a row ... (07-30, 08-04, 08-31)" --
    and by the time anyone read it again the true count was SIX. A number a
    human is asked to act on cannot live in an f-string that no run updates.

    The per-wave verdict comes from refresh_yield(), the SAME function the
    postcheck's own headline verdict comes from, so the streak and the headline
    can never disagree (one extractor, the FU-108 lesson). Waves with no
    refresh half (refresh_yield -> None) are SKIPPED rather than counted as
    breaks: they had nothing to be zero about. An `unmeasured` wave BREAKS the
    streak, because R6 -- we never asked, so we cannot claim it read zero.

    Returns {"streak", "dates", "basis"}. streak 0 when nothing can be read,
    which is honest rather than reassuring.
    """
    root = RUNS_ROOT if runs_root is None else Path(runs_root)
    try:
        rids = sorted((p.name for p in root.iterdir() if p.is_dir()), reverse=True)
    except OSError as e:                                      # noqa: BLE001
        # Same keys as the success exit below. ph_postcheck reads
        # `skipped_unmeasured` unconditionally, so an error dict missing it turns
        # the branch that exists to say UNKNOWN into a KeyError -- which is how
        # this shipped red. 0 here means NOTHING WAS SCANNED, not "nothing was
        # skipped"; `basis` is what distinguishes the two.
        return {"streak": 0, "dates": [], "skipped_unmeasured": 0,
                "basis": f"runs root unreadable ({e}) -- UNKNOWN, not zero"}
    streak, dates, scanned, skipped = 0, [], 0, 0
    for rid in rids:
        try:
            st = json.loads((root / rid / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ry = refresh_yield(st)
        if ry is None:
            continue                      # no refresh half: nothing to judge
        if ry["verdict"] == "unmeasured":
            # A wedged/abandoned wave never LANDED, so it is not a
            # counterexample to "N consecutive landed waves produced nothing"
            # -- it is not in the population. Breaking here would UNDERSTATE
            # the streak, which is the defect this function replaced. R6 is
            # satisfied by never claiming it read zero, and by publishing the
            # skip count rather than hiding it.
            skipped += 1
            continue
        scanned += 1
        if ry["verdict"] != "zero_yield":
            break                         # a PRODUCTIVE wave ends the streak
        streak += 1
        dates.append(rid[4:6] + "-" + rid[6:8])
    return {"streak": streak, "dates": dates, "skipped_unmeasured": skipped,
            "basis": f"refresh_yield() over each run's own state.json, newest "
                     f"first, under {root}; {scanned} landed wave(s) with a "
                     f"refresh half inspected, {skipped} unmeasured wave(s) "
                     f"skipped (never landed, so not a counterexample)"}

def ph_postcheck(run: Run, args) -> None:
    if run.done("postcheck"):
        return
    # FU-027: NON-FATAL. Every write this run makes is already committed by the time
    # postcheck runs (import -> backfill -> postcheck). If the freshness surface is
    # unreachable we still close the run and record WHY the comparison is missing,
    # because an open run is invisible to the liveness rule (FU-056) whereas a
    # degraded report is loud and harmless.
    after, freshness_error = freshness_safe()
    if freshness_error:
        log(f"POSTCHECK DEGRADED: freshness unreadable ({freshness_error}); "
            f"closing the run anyway -- all writes were already committed")
    base = run.state.get("baseline_freshness", {})
    # FU-132: when /freshness is unreachable the run still KNOWS its post-state --
    # `import` stamped `scored_after` from a DIRECT DB read, which is the very number
    # I1 is enforced against and a strictly better source than the cached endpoint.
    # Run 20260727-105859 shipped `scored_servers.after: null` while its own
    # state.json held 279116: the artifact a successor reads was less true than the
    # state the run was holding. Publish the figure AND its basis -- a number without
    # its basis is how "MTD spend" became a 24h delta (FU-035).
    scored_after = after.get("scored_servers")
    after_basis = "freshness" if scored_after is not None else None
    if scored_after is None and run.state.get("scored_after") is not None:
        scored_after = run.state["scored_after"]
        after_basis = "db_import"
        log(f"postcheck: freshness unreadable; scored_servers.after={scored_after} "
            f"taken from the import phase's direct DB read (basis=db_import)")
    ry = refresh_yield(run.state)
    if ry and ry["verdict"] == "zero_yield":
        zs = zero_yield_streak()
        log(f"POSTCHECK: REFRESH HALF PRODUCED NOTHING -- {ry['changed']} of "
            f"{ry['refresh_servers']} refreshed servers changed on any axis "
            f"({ry['rate']:.4%}). The never-scored half is where this wave's "
            f"value came from. This is CONSECUTIVE ZERO-YIELD WAVE "
            f"#{zs['streak']} ({', '.join(zs['dates']) or 'none recorded'}"
            f"{'; %d unmeasured wave(s) skipped' % zs['skipped_unmeasured'] if zs['skipped_unmeasured'] else ''}) -- "
            f"streak DERIVED, not restated ({zs['basis']}). The refresh cap is "
            f"buying nothing and the cohort budget belongs elsewhere -- that is "
            f"a peer-review decision, not an abort.")
        ledger("refresh_zero_yield", run.state["run_id"],
               consecutive=zs["streak"], streak_dates=zs["dates"], **ry)
    elif ry and ry["verdict"] == "unmeasured":
        log(f"POSTCHECK: refresh half UNMEASURED ({ry['basis']}). "
            f"Not a reading of zero.")
    elif ry:
        log(f"postcheck: refresh half changed {ry['changed']} of "
            f"{ry['refresh_servers']} ({ry['rate']:.2%})")
    report = {
        "run_id": run.state["run_id"], "mode": run.state["mode"],
        "refresh_yield": ry,
        "freshness_error": freshness_error,
        "degraded_postcheck": bool(freshness_error),
        "exported": run.state.get("exported"), "imported": run.state.get("imported_servers"),
        "coverage": run.state.get("coverage"), "degraded": run.state.get("degraded"),
        "est_cost_usd": run.state.get("est_cost"),
        "scores_rows": {"before": base.get("scores_rows"), "after": after.get("scores_rows")},
        "scored_servers": {"before": base.get("scored_servers"),
                           "after": scored_after, "after_basis": after_basis},
        "newest_scored_at": {"before": base.get("newest_scored_at"),
                             "after": after.get("newest_scored_at")},
        "destroyed": run.state.get("destroyed", False) or
                     run.state["phases"].get("fire") == "skipped",
    }
    (run.dir / "report.json").write_text(json.dumps(report, indent=1))
    run.mark("postcheck", report=report)
    ledger("run_closed", run.state["run_id"], **{k: report[k] for k in
           ("exported", "imported", "est_cost_usd", "degraded")})
    log("REPORT " + json.dumps(report))


# ---------------------------------------------------------------- main
FIRE_PHASES = [ph_preflight, ph_export, ph_bundle, ph_fire]
COLLECT_PHASES = [ph_watch_collect, ph_import, ph_backfill, ph_postcheck]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="all phases, one process")
    ap.add_argument("--phase", choices=["fire-all", "collect-all"],
                    help="scheduled-task halves")
    ap.add_argument("--check-open-runs", action="store_true",
                    help="FU-056: report runs opened but never closed; "
                         "exit 1 if any is older than the stale threshold")
    ap.add_argument("--full", action="store_true", help="full rescore (default: delta)")
    ap.add_argument("--refresh-cap", type=int, default=REFRESH_CAP_DEFAULT)
    ap.add_argument("--max-dph", type=float, default=MAX_DPH_DEFAULT)
    ap.add_argument("--cost-cap", type=float, default=None,
                    help="override the FU-090 size-scaled cap (default: scaled)")
    ap.add_argument("--cap-usd", type=float, default=None,
                    help="authority per-wave ceiling (F8): measured vast spend since "
                         "launch >= this destroys the instance and prints CAP_HIT")
    ap.add_argument("--deadline-min", type=int, default=None,
                    help="override the FU-090 size-scaled deadline (default: scaled)")
    ap.add_argument("--poll-secs", type=int, default=120)
    ap.add_argument("--wedge-grace-min", type=int,
                    default=WEDGE_GRACE_MIN_DEFAULT,
                    help="fail fast if the instance never reaches 'running' within this many minutes")
    args = ap.parse_args()
    if args.check_open_runs:
        raise SystemExit(check_open_runs())
    if args.cap_usd is not None and not args.cap_usd > 0:
        ap.error("--cap-usd must be > 0 (a zero or negative grant is a refusal, not a cap)")
    if not (args.run or args.phase):
        ap.error("need --run, --phase or --check-open-runs")
    mode = "full" if args.full else "delta"
    try:
        if args.phase == "collect-all":
            run = open_run(None)
        else:
            run = open_run(mode)
        phases = (FIRE_PHASES + COLLECT_PHASES if args.run
                  else FIRE_PHASES if args.phase == "fire-all" else COLLECT_PHASES)
        for ph in phases:
            ph(run, args)
        log("weekly_rescore: all requested phases complete")
    finally:
        if _PROXY:
            _PROXY.terminate()
