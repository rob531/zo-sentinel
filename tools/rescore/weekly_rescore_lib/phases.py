# ruff: noqa: F821  -- names injected into globals at exec time by weekly_rescore.py
# weekly_rescore_lib/phases.py -- PART OF weekly_rescore.py, not a standalone module.
# export, bundle, fire, vast instance lifecycle, watch and collect phases.
# weekly_rescore.py runs this file via _weekly_rescore_lib_exec("phases") INSIDE
# weekly_rescore's own namespace, at the exact position this code stood
# (original lines 824-1507), so every name here is still
# weekly_rescore.<name> and __file__ is weekly_rescore.py.
# Do not import it directly; callers keep citing weekly_rescore.<name>.
# (chain rebase_godfiles S20, 2026-11-02; map: weekly_rescore_lib/README.md)
from __future__ import annotations


def ph_export(run: Run, args) -> None:
    if run.done("export"):
        return
    conn = pg_conn()
    # v3 (2026-07-25, FU-100): the old path streamed the FULL registry WITH
    # descriptions and was the export bottleneck (~80 rows/s, payload-bound over
    # the fly proxy). The 1GB Fly PG still spills on server-side anti-joins/windows
    # (the 2026-07-14 finding), so the set logic stays client-side -- but we now
    # stream only the LEAN keys (id,url,scorable), do the distinct-URL + new/refresh
    # split on those, then fetch descriptions for the bounded COHORT only. Identical
    # cohort; ~15x less wire payload.
    cur = conn.cursor("reg_keys")
    cur.itersize = 5000
    cur.execute(SQL_REGISTRY_KEYS)
    reg = []
    t0 = time.time()
    for row in cur:
        reg.append(row)
        if len(reg) % 50000 == 0:
            log(f"export: key stream {len(reg)} rows "
                f"({len(reg)/(time.time()-t0):.0f} rows/s)")
    cur.close()
    cur2 = conn.cursor()
    cur2.execute(SQL_SCORED)
    scored_at = dict(cur2.fetchall())         # sid -> scored_at
    log(f"export: streamed keys={len(reg)} scored={len(scored_at)}")

    def ukey(sid, url):
        return url or sid

    # distinct-URL representative (order by (url_key, server_id), keep first),
    # preferring an already-scored rep so the refresh lane refreshes in place.
    reg.sort(key=lambda r: (ukey(r[0], r[1]), r[0]))
    reps = {}
    for r in reg:
        k = ukey(r[0], r[1])
        cur_rep = reps.get(k)
        if cur_rep is None:
            reps[k] = r
        elif cur_rep[0] not in scored_at and r[0] in scored_at:
            reps[k] = r                        # scored rep wins for refresh
    new_rows, refresh_rows = [], []
    for k, r in reps.items():
        if r[0] in scored_at:
            refresh_rows.append(r)
        elif r[2]:                             # scorable (verdict unreviewed/unknown)
            new_rows.append(r)
    # FU-093 trust priority (2026-07-26). A garbage score is WORSE than no
    # score: it is served to customers as though it were real. Plain
    # oldest-first is a trap here -- the three random-head waves carry the
    # NEWEST scored_at values, so age-ordering sorts the known-garbage cohorts
    # LAST and the weekly cadence would never reach them. Rank distrust ahead
    # of age; within each group, oldest first as before.
    distrusted: set = set()
    try:
        distrusted, verdicts = cohort_trust(conn)
        bad_servers = sum(1 for r in refresh_rows if scored_at[r[0]] in distrusted)
        log(f"export: trust audit -- {len(verdicts)} cohorts, "
            f"{len(distrusted)} distrusted ({bad_servers} servers) "
            f"prioritised ahead of age")
    except Exception as exc:      # FAIL-SAFE: this must never block an export
        distrusted = set()
        log(f"export: trust audit FAILED ({exc.__class__.__name__}: {exc}) -- "
            f"falling back to oldest-first ordering; FU-093 priority NOT applied")
    # V06 lifecycle_changed priority: read the changed-priority queue so that
    # servers with meaningful changes (description, url, cert/CA, verdict) are
    # prepended AHEAD of distrusted/age cohorts.  Fail-safe: an empty or absent
    # queue file reduces to the existing (trust_rank, scored_at) two-key sort.
    changed_priority = _read_changed_priority()
    if changed_priority:
        n_changed = sum(1 for r in refresh_rows if r[0] in changed_priority)
        log(f"export: changed-priority queue: {n_changed} servers moved to front "
            f"(total unconsumed queue size: {len(changed_priority)})")
    # sort key = (changed_rank, trust_rank, scored_at):
    #   changed first (False=0), then distrusted, then oldest-scored.
    refresh_rows.sort(key=lambda r: (r[0] not in changed_priority,
                                     scored_at[r[0]] not in distrusted,
                                     scored_at[r[0]]))
    if run.state["mode"] != "full":
        try:
            refresh_rows, note = snap_refresh_to_cohort(
                refresh_rows, args.refresh_cap, scored_at,
                lambda sids: residue_verdict(conn, sids))
        except Exception as exc:      # FAIL-SAFE: never block an export
            refresh_rows = refresh_rows[:args.refresh_cap]
            note = f"snap: FAILED ({exc.__class__.__name__}: {exc}) -- plain slice"
        log(f"export: {note}")
        run.state["refresh_snap"] = note
    # BOUNDED NEW-WAVE CAP (2026-10-15). new_rows (never-scored-under-this-version)
    # is otherwise UNCAPPED, so a brand-new model_version would try to score the whole
    # corpus in one pod. RESCORE_NEW_CAP=N bounds each wave to N never-scored servers,
    # chosen deterministically (sorted by server_id) so sequential waves tile the corpus
    # and a resume picks up exactly where the last wave stopped -- the pipeline's
    # (server_id, model_version) dedup makes any overlap converge rather than double-score.
    # Unset -> unbounded, so the scheduled v3.0 delta lane is unchanged. Refresh half is
    # already capped by --refresh-cap; this is the matching knob for the never-scored half.
    _new_cap = os.environ.get("RESCORE_NEW_CAP")
    if _new_cap and _new_cap.isdigit() and int(_new_cap) > 0:
        new_rows.sort(key=lambda r: r[0])
        _nc = int(_new_cap)
        log(f"export: RESCORE_NEW_CAP={_nc} -- bounding never-scored half "
            f"{len(new_rows)} -> {min(_nc, len(new_rows))} (sorted by server_id)")
        new_rows = new_rows[:_nc]
    uniq = new_rows + refresh_rows
    cohort_sids = [r[0] for r in uniq]

    # descriptions for the COHORT only (server_id is PK -> indexed, no spill)
    detail = {}
    if cohort_sids:
        cur3 = conn.cursor()
        cur3.execute(SQL_DETAIL, (cohort_sids,))
        for d_sid, d_name, d_src, d_url, d_descr in cur3:
            detail[d_sid] = (d_name, d_src, d_url, d_descr)
        cur3.close()
    conn.close()

    inp = run.dir / "inputs.jsonl.gz"
    with gzip.open(inp, "wt", encoding="utf-8") as f:
        for sid in cohort_sids:
            d = detail.get(sid)
            if d is None:
                continue
            name, src, url, descr = d
            hdr = (f"MCP SERVER UNDER REVIEW:\n  server_id: {sid}\n  name:      {name}\n"
                   f"  source:    {src}\n  url:       {url}\n  description: {descr}\n\n")
            f.write(json.dumps({"messages": [{"role": "system", "content": SYS},
                                             {"role": "user", "content": hdr + SIG}],
                                "metadata": {"server_id": sid}}) + "\n")
    run.mark("export", exported=len(uniq), new_servers=len(new_rows),
             refresh_servers=len(refresh_rows))
    log(f"export OK: {len(uniq)} inputs ({len(new_rows)} never-scored + "
        f"{len(refresh_rows)} refresh)")
    run.state["cost_cap_scaled"] = round(scaled_budget(len(uniq)), 2)
    run.state["deadline_scaled"] = scaled_deadline_min(len(uniq))
    run.save()
    log(f"spend guard (FU-090 #1784): N={len(uniq)} -> cost_cap "
        f"${run.state['cost_cap_scaled']:.2f}, deadline {run.state['deadline_scaled']}m")
    if len(uniq) == 0:
        run.mark("bundle", "skipped"); run.mark("fire", "skipped")
        run.mark("watch", "skipped"); run.mark("collect", "skipped")
        run.mark("destroy", "skipped"); run.mark("import", "skipped")
        run.mark("backfill", "skipped")
        log("nothing to score -- run will close at postcheck")


# --------------------------------------------------- FU-105: ls-tree -l parsing
# `git ls-tree -r -l` emits FIVE fields, because -l inserts an object SIZE column:
#
#   100644 blob 934805596ff4...eba6c 29528024\tscore_transfer/adapter/adapter_model.safetensors
#   <mode> <type> <sha>              <size>  \t<path>
#     f[0]  f[1]   f[2]               f[3]      f[4]
#
# FU-093 shipped index math for the FOUR-field (`-r`, no `-l`) shape: it read f[3]
# as the PATH (it is the SIZE) and f[2] as the SIZE (it is the SHA). Consequences,
# both silent:
#   * the post-push REMOTE verify could NEVER return ok -> it aborted every single
#     bundle, even when the 28.2MB adapter had landed perfectly (run 20260726-014732);
#   * the 133-byte LFS-POINTER guard could NEVER fire, because its path test was
#     comparing against a number -- a DECORATIVE gate, the exact failure class
#     FU-093 was written to eliminate.
# One parser now serves both call sites, and it is pinned by tests built from real
# captured `git ls-tree -r -l` bytes rather than a hand-written fixture.
def adapter_blob_size(tree_text: str, name: str = "adapter_model.safetensors"):
    """Size in bytes of `name` in `git ls-tree -r -l` output, or None if absent."""
    for line in tree_text.splitlines():
        f = line.split()
        if len(f) >= 5 and f[4].endswith(name) and f[3].isdigit():
            return int(f[3])
    return None


def ph_bundle(run: Run, args) -> None:
    if run.done("bundle") or run.state["phases"].get("bundle") == "skipped":
        return
    pat = secret("github")
    ts = run.state["run_id"]
    score_branch = f"score-job-{ts}"
    repo_url = f"https://x-access-token:{pat}@github.com/{SFT_REPO}.git"
    # unique dir per attempt: Windows file locks make rmtree unreliable, and a
    # half-deleted dir fails `git clone` with rc=128.
    work = run.dir / f"sft_{int(time.time())}"
    def git(*a, **kw):
        r = subprocess.run(["git", *a], cwd=kw.pop("cwd", work), capture_output=True,
                           text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"git {a[0]} failed: {r.stderr[-300:]}")
        return r
    r0 = subprocess.run(["git", "clone", "--depth", "1", repo_url, str(work)],
                        capture_output=True, text=True, timeout=900)
    if r0.returncode != 0:
        raise RuntimeError("git clone failed: " +
                           r0.stderr[-300:].replace(pat, "***"))
    (work / "score_transfer/adapter").mkdir(parents=True, exist_ok=True)
    for p in ADAPTER_LOCAL_DIR.iterdir():
        if p.is_file():
            shutil.copy(p, work / "score_transfer/adapter" / p.name)
    # I5: adapter sha pin
    shas = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (work / "score_transfer/adapter").iterdir() if p.is_file()}
    if ADAPTER_SHA_PIN not in shas.values():
        raise SystemExit(f"ABORT: adapter sha pin {ADAPTER_SHA_PIN[:12]}.. not found in "
                         f"score_transfer/adapter ({ {k: v[:12] for k, v in shas.items()} })")
    shutil.copy(run.dir / "inputs.jsonl.gz", work / "score_transfer/inputs.jsonl.gz")
    git("checkout", "--orphan", score_branch)
    git("rm", "-r", "--cached", ".")
    # FU-093: -f is MANDATORY. The SFT repo .gitignore lists *.safetensors, *.pt,
    # *.bin, so a plain git add SILENTLY skips the adapter weights + heads. The
    # pod then gets only adapter_config.json, PEFT cannot attach, and eval scores
    # on base + RANDOM HEADS while reporting success (3 weeks of garbage:
    # 07-18/07-21/07-24; same class as the RunPod-era "weights keep vanishing").
    git("add", "-f", "score_transfer")
    git("-c", "user.email=tower@zo", "-c", "user.name=weekly-rescore",
        "commit", "-q", "-m", f"score transfer bundle {ts}")
    # FU-093 I5b: verify the COMMITTED TREE, not the filesystem. The old I5
    # hashed the local copy, so it passed green while git had silently dropped
    # the weights (.gitignore). Also catch the known 133-byte LFS-POINTER
    # signature (documented: 'pointers, the real 29.5MB weights live elsewhere').
    tree = git("ls-tree", "-r", "-l", "HEAD", "score_transfer/adapter").stdout
    for need in ("adapter_model.safetensors", "heads_state_dict.pt"):
        if need not in tree:
            raise SystemExit(f"ABORT: {need} missing from the COMMITTED bundle -- "
                             f".gitignore swallowed it. tree=\\n{tree}")
    csize = adapter_blob_size(tree)
    if csize is None:
        raise SystemExit(f"ABORT: adapter_model.safetensors not parseable in the "
                         f"COMMITTED tree. tree=\\n{tree}")
    if csize < 1_000_000:
        raise SystemExit(f"ABORT: committed adapter is {csize}B -- an LFS pointer/stub, "
                         f"not weights (the 133-byte failure class).")
    git("push", repo_url, f"HEAD:refs/heads/{score_branch}")
    # FU-093: NEVER trust the push exit code (standing SFT lesson). Re-read the
    # REMOTE tree and assert real bytes actually landed.
    git("fetch", "--depth", "1", repo_url, score_branch)
    rtree = git("ls-tree", "-r", "-l", "FETCH_HEAD", "score_transfer/adapter").stdout
    rsize = adapter_blob_size(rtree)
    ok = rsize is not None and rsize >= 1_000_000
    if not ok:
        raise SystemExit(f"ABORT: post-push REMOTE verify failed -- adapter weights did "
                         f"not land on {score_branch}. remote tree=\\n{rtree}")
    log("bundle verify: adapter weights confirmed on the remote branch")
    run.mark("bundle", score_branch=score_branch,
             results_branch=f"{score_branch}-results")
    log(f"bundle OK: pushed {score_branch} (adapter pin verified)")


def ph_fire(run: Run, args) -> None:
    if run.done("fire") or run.state["phases"].get("fire") == "skipped":
        return
    from vastai_sdk import VastAI
    v = VastAI(api_key=secret("vast"))
    max_dph = args.max_dph

    def geo_ok(o):
        return not any(b in str(o.get("geolocation", "")).upper() for b in GEO_BLOCK)

    blocklist_path = RUNS_ROOT / "wedged_machines.json"
    wedged_machines = (set(json.loads(blocklist_path.read_text()))
                       if blocklist_path.exists() else set())
    if wedged_machines:
        log(f"offer filter: excluding {len(wedged_machines)} wedged machine(s)")

    def cheapest(q):
        offers = v.search_offers(query=q)
        c = [o for o in offers if geo_ok(o) and float(o.get("dph_total", 99)) <= max_dph
             and o.get("machine_id") not in wedged_machines]
        return min(c, key=lambda o: float(o["dph_total"])) if c else None

    best = cheapest(f"gpu_name=RTX_4090 num_gpus=1 dph_total<{max_dph} verified=true "
                    f"rentable=true disk_space>=50 inet_down>=200")
    if not best:
        best = cheapest(f"gpu_name in [RTX_4090,L40S,L40,RTX_3090,A40,A6000] num_gpus=1 "
                        f"dph_total<{max_dph} verified=true rentable=true disk_space>=50")
    if not best:
        raise SystemExit("ABORT: no eligible GPU offer under price/geo guard")
    dph = float(best["dph_total"])
    launch_credit = _read_launch_credit(args)   # BEFORE the rental starts billing
    resp = v.create_instance(
        id=best["id"], image=IMAGE, disk=50,
        env={"RUnpodGHAPI": secret("github"),
             "SCORE_BRANCH": run.state["score_branch"],
             "RESULTS_BRANCH": run.state["results_branch"]},
        onstart_cmd=ONSTART, runtype="ssh", label="zo-sentinel-score")
    iid = resp.get("new_contract") or resp.get("contract_id") or resp.get("id")
    run.state["credit_at_launch"] = launch_credit
    if getattr(args, "cap_usd", None) is not None:
        run.state["cap_usd"] = float(args.cap_usd)   # a later collect-all still enforces it
    run.mark("fire", instance_id=iid, dph=dph, gpu=best.get("gpu_name"),
             machine_id=best.get("machine_id"), fired_at=utcnow())
    log(f"fire OK: instance={iid} {best.get('gpu_name')} ${dph}/hr "
        f"(cap ${_eff_cost_cap(run, args):.2f}, deadline {_eff_deadline(run, args)}m)")


def _results_state(run: Run, pat: str) -> str:
    """'', 'ok' or 'fail' depending on which results branch exists."""
    url = f"https://x-access-token:{pat}@github.com/{SFT_REPO}.git"
    r = subprocess.run(["git", "ls-remote", "--heads", url,
                        run.state["results_branch"], run.state["results_branch"] + "-fail"],
                       capture_output=True, text=True, timeout=120)
    out = r.stdout or ""
    if run.state["results_branch"] + "-fail" in out:
        return "fail"
    if run.state["results_branch"] in out:
        return "ok"
    return ""


def _destroy(run: Run, reason: str) -> None:
    from vastai_sdk import VastAI
    iid = run.state.get("instance_id")
    if not iid or run.state.get("destroyed"):
        return
    v = VastAI(api_key=secret("vast"))
    try:
        v.destroy_instance(id=int(iid))
    except Exception as e:                                 # noqa: BLE001
        log(f"destroy call error ({e}); verifying via list")
    time.sleep(10)
    alive = [i for i in (v.show_instances() or []) if str(i.get("id")) == str(iid)]
    if alive:
        ledger("destroy_FAILED", run.state["run_id"], instance=iid)
        raise SystemExit(f"ALERT: instance {iid} still alive after destroy -- manual action needed")
    run.state["destroyed"] = True
    run.save()
    ledger("destroyed", run.state["run_id"], instance=iid, reason=reason)
    log(f"instance {iid} destroyed ({reason})")


def _eff_cost_cap(run, args):
    """CLI --cost-cap overrides; else size-scaled value fixed at export (FU-090)."""
    if getattr(args, "cost_cap", None) is not None:
        return args.cost_cap
    return run.state.get("cost_cap_scaled", COST_CAP_DEFAULT)


def _eff_deadline(run, args):
    if getattr(args, "deadline_min", None) is not None:
        return args.deadline_min
    return run.state.get("deadline_scaled", DEADLINE_MIN_DEFAULT)


def _eff_cap_usd(run, args):
    """Authority ceiling (F8): CLI --cap-usd, else the value stamped at fire. None = no grant cap."""
    if getattr(args, "cap_usd", None) is not None:
        return float(args.cap_usd)
    cap = run.state.get("cap_usd")
    return float(cap) if cap is not None else None


def _vast_credit() -> float:
    from vast_spend import remaining_credit
    return remaining_credit()


def _read_launch_credit(args):
    """Account credit just before the rental. None when unreadable -- the watch
    loop then falls back to the billed-dph estimate, so the cap still binds."""
    if getattr(args, "cap_usd", None) is None:
        return None
    try:
        return _vast_credit()
    except Exception as e:  # noqa: BLE001 -- a spend READ must never block a launch
        log(f"cap: launch credit unreadable ({e.__class__.__name__}: {e}); "
            f"cap will be enforced on the billed-dph estimate")
        return None


def _spent_since_launch(run, est_cost: float) -> tuple[float, str]:
    """(spend, basis). MEASURED credit delta when readable, never below the estimate."""
    c0 = run.state.get("credit_at_launch")
    if c0 is not None:
        try:
            measured = round(float(c0) - _vast_credit(), 4)
            if measured >= est_cost:
                return measured, "vast_credit_delta"
            return est_cost, "billed_dph_estimate(>credit_delta)"
        except Exception as e:  # noqa: BLE001
            log(f"cap: credit read failed ({e.__class__.__name__}); using estimate")
    return est_cost, "billed_dph_estimate"


def _billed_dph(run, args) -> float:
    """The rate we are ACTUALLY billed, not the rate we were quoted.

    `fire` stamps state["dph"] from the OFFER's dph_total, which prices compute
    only. Once the instance is rented, vast adds the allocated-storage component,
    so the live instance's dph_total is strictly >= the offer's. Observed on run
    20260727-105859: offer 0.296111, live 0.321111 -- an 8.4% under-count, which
    means the COST_CAP_USD ceiling fires ~8% late and every "est $x" line we log
    understates the bill. Same failure shape as the MTD spend guard (FU-035):
    a guard is only as honest as the number it compares against.

    Falls back to the stamped offer dph if the API is unreachable -- a watch loop
    must never die because vast is having a moment. Returns the LARGER of the two
    so a lookup failure can never lower the ceiling's basis.
    """
    quoted = float(run.state.get("dph", args.max_dph))
    iid = run.state.get("instance_id")
    if not iid:
        return quoted
    try:
        from vastai_sdk import VastAI
        v = VastAI(api_key=secret("vast"))
        for i in (v.show_instances() or []):
            if str(i.get("id")) == str(iid):
                live = float(i.get("dph_total") or 0)
                if live > quoted:
                    log(f"watch: billed dph {live:.6f}/hr > quoted {quoted:.6f}/hr "
                        f"(storage component); using billed for the cost ceiling")
                    return live
                return quoted
    except Exception as e:
        log(f"watch: live dph lookup failed ({e.__class__.__name__}: {e}); "
            f"falling back to quoted ${quoted:.6f}/hr")
    return quoted


WEDGE_GRACE_MIN_DEFAULT = 25
POD_PROGRESS_EVERY_SECS = 300


def _instance_probe(run) -> dict:
    """Best-effort live status of the fired instance.

    Returns {} when the API is unreachable (UNKNOWN, not absent -- R6).
    Returns {"present": False} when the API answered and the instance is
    genuinely missing from the list (outbid, host reclaim, manual kill).
    Run 20260822-220319 burned its whole 212m deadline against an instance
    nobody ever looked at; this is the look.
    """
    iid = run.state.get("instance_id")
    if not iid:
        return {}
    try:
        from vastai_sdk import VastAI
        v = VastAI(api_key=secret("vast"))
        rows = v.show_instances()
        if not rows:
            return {}
        for i in rows:
            if str(i.get("id")) == str(iid):
                return {"present": True,
                        "actual_status": i.get("actual_status"),
                        "status_msg": (i.get("status_msg") or "")[:200],
                        "cur_state": i.get("cur_state")}
        return {"present": False}
    except Exception as e:  # noqa: BLE001
        log(f"watch: instance probe failed ({e.__class__.__name__}: {e}); status UNKNOWN")
        return {}



def _pod_progress(run) -> str:
    """Last scoring-progress fragment the pod emitted, via the vast logs API.

    Report-only, best effort, never raises: an unreadable API yields "" and the
    caller says nothing rather than something false (R6 -- unknown is not zero).
    Cached for POD_PROGRESS_EVERY_SECS so the watch loop does not hammer it.
    """
    import hashlib as _hashlib
    import re as _re
    now = time.time()
    last = run.state.get("_pod_progress_at", 0)
    if now - last < POD_PROGRESS_EVERY_SECS:
        return run.state.get("_pod_progress", "")
    iid = run.state.get("instance_id")
    if not iid:
        return ""
    text = ""
    try:
        from vastai_sdk import VastAI
        v = VastAI(api_key=secret("vast"))
        for call in (lambda: v.logs(INSTANCE_ID=int(iid)),
                     lambda: v.logs(id=int(iid)),
                     lambda: v.logs(int(iid))):
            try:
                text = call() or ""
            except TypeError:
                continue
            if text:
                break
    except Exception as e:  # noqa: BLE001
        log(f"watch: pod progress unreadable ({e.__class__.__name__}); progress UNKNOWN")
        text = ""
    frag = ""
    for m in _re.finditer(r"(\d+)/(\d+)\s*\[", str(text)):
        done, total = m.group(1), m.group(2)
        if total and int(total) > 100:      # the inputs bar, not a 2-shard loader
            frag = f"{done}/{total}"
    # The vast logs API can serve a FROZEN snapshot: on instance 50335633 it
    # returned byte-identical text (md5 0d5d3168, 5642 B) across 5 calls and 12
    # minutes, tail stuck at `7/28601`, with and without `tail=`. A number
    # carried forward is indistinguishable from a number measured, so publish
    # the fragment only while the snapshot is actually moving, and say once
    # that the instrument is blind rather than printing a comfortable digit.
    digest = _hashlib.md5(str(text).encode("utf-8", "replace")).hexdigest()
    if text and digest == run.state.get("_pod_log_md5"):
        if not run.state.get("_pod_log_frozen_said"):
            log("watch: vast log snapshot unchanged between polls -- pod progress "
                "UNAVAILABLE (frozen API, not a stalled pod)")
            run.state["_pod_log_frozen_said"] = True
        frag = ""
    else:
        run.state["_pod_log_md5"] = digest
        run.state["_pod_log_frozen_said"] = False
    run.state["_pod_progress"] = frag
    run.state["_pod_progress_at"] = now
    run.save()
    return frag


def _watch_basis(run, probe: dict) -> str:
    """The status the loop ALREADY holds, rendered into the line a human reads.

    Run 20260909-014759 spent 40 minutes printing "no results yet" while
    state.json recorded status_seen == [unknown, loading, running] and the pod
    was 30% through its cohort. Nothing was broken; the reading was simply
    silent about the one fact that separates a wedge from a slow job. A value
    measured, stored and then omitted from the only surface anyone reads is
    this fleet's most expensive recurring shape (cf. FU-358).
    """
    if not probe:
        status = "UNKNOWN (probe unreadable)"
    elif probe.get("present") is False:
        status = "absent"
    else:
        status = probe.get("actual_status") or "unknown"
    out = f", status {status}"
    prog = _pod_progress(run)
    if prog:
        out += f", pod {prog}"
    return out

def _pull_instance_logs(run) -> None:
    """SSH-free forensics BEFORE destroy, via the vast logs API.

    The existing forensic path (the pod pushes a git branch) is exactly what
    fails when the pod never boots -- 3 waves died with zero artifacts that
    way. This path depends only on the vast API and must never raise.
    """
    iid = run.state.get("instance_id")
    if not iid:
        return
    coll = run.dir / "results"
    coll.mkdir(exist_ok=True)
    try:
        from vastai_sdk import VastAI
        v = VastAI(api_key=secret("vast"))
        text = None
        for call in (lambda: v.logs(INSTANCE_ID=int(iid)),
                     lambda: v.logs(id=int(iid)),
                     lambda: v.logs(int(iid))):
            try:
                text = call()
            except TypeError:
                continue
            if text:
                break
        if text:
            (coll / "vast_instance.log").write_text(
                str(text)[-200_000:], encoding="utf-8")
            log(f"forensics: saved vast instance log ({len(str(text))} chars)")
        else:
            log("forensics: vast logs API returned nothing")
    except Exception as e:  # noqa: BLE001
        log(f"forensics: vast logs fetch failed ({e.__class__.__name__}: {e})")


def ph_watch_collect(run: Run, args) -> None:
    if run.done("collect") or run.state["phases"].get("collect") == "skipped":
        return
    pat = secret("github")
    fired = datetime.fromisoformat(run.state["fired_at"])
    dph = _billed_dph(run, args)
    while True:
        st = _results_state(run, pat)
        elapsed_h = (datetime.now(timezone.utc) - fired).total_seconds() / 3600
        est_cost = elapsed_h * dph
        if st:
            run.mark("watch", result=st, est_cost=round(est_cost, 2))
            break
        probe = _instance_probe(run)
        if probe:
            stat = (probe.get("actual_status")
                    or ("absent" if probe.get("present") is False else "unknown"))
            seen = run.state.setdefault("status_seen", [])
            if not seen or seen[-1] != stat:
                seen.append(stat)
                run.save()
                ledger("instance_status", run.state["run_id"], status=stat,
                       msg=probe.get("status_msg", ""))
            if probe.get("present") is False:
                miss = run.state.get("absent_probes", 0) + 1
                run.state["absent_probes"] = miss
                run.save()
                if miss >= 2:
                    # Two consecutive authoritative "not in the list" answers:
                    # waiting out the deadline can never produce a result.
                    ledger("instance_vanished", run.state["run_id"],
                           elapsed_h=round(elapsed_h, 2))
                    run.mark("watch", "failed", result="vanished")
                    break
            else:
                if run.state.get("absent_probes"):
                    run.state["absent_probes"] = 0
                    run.save()
                if ("running" not in run.state.get("status_seen", [])
                        and elapsed_h * 60 >= getattr(
                            args, "wedge_grace_min", WEDGE_GRACE_MIN_DEFAULT)):
                    # Never reached "running" inside the grace window: machine
                    # wedge, not a slow job. Blocklist + fail fast.
                    mid = run.state.get("machine_id")
                    if mid is not None:
                        blk = RUNS_ROOT / "wedged_machines.json"
                        try:
                            cur = set(json.loads(blk.read_text())) if blk.exists() else set()
                        except Exception:  # noqa: BLE001
                            cur = set()
                        if mid not in cur:
                            cur.add(mid)
                            blk.write_text(json.dumps(sorted(cur)))
                            log(f"wedge: machine {mid} added to blocklist")
                    ledger("wedge_watch_destroy", run.state["run_id"],
                           machine=mid, last_status=stat,
                           elapsed_h=round(elapsed_h, 2))
                    run.mark("watch", "failed", result="wedge")
                    break
        cap_usd = _eff_cap_usd(run, args)
        if cap_usd is not None:
            spent, basis = _spent_since_launch(run, est_cost)
            if spent >= cap_usd:
                print(f"CAP_HIT run={run.state['run_id']} spent=${spent:.2f} "
                      f"cap=${cap_usd:.2f} basis={basis} -- destroying instance",
                      flush=True)
                ledger("cap_hit", run.state["run_id"], spent=round(spent, 4),
                       cap_usd=cap_usd, basis=basis)
                run.mark("watch", "failed", result="cap_hit", spent_usd=round(spent, 4))
                break
        if est_cost >= _eff_cost_cap(run, args):
            ledger("cost_ceiling_breach", run.state["run_id"], est=est_cost)
            run.mark("watch", "failed", result="cost_breach")
            break
        if elapsed_h * 60 >= _eff_deadline(run, args):
            ledger("deadline_breach", run.state["run_id"], elapsed_h=round(elapsed_h, 2))
            run.mark("watch", "failed", result="deadline")
            break
        log(f"watch: no results yet (elapsed {elapsed_h:.2f}h, est ${est_cost:.2f}"
            f"{_watch_basis(run, probe)})")
        time.sleep(args.poll_secs)
    # COLLECT (forensics ALWAYS -- I3), from ok or fail branch
    url = f"https://x-access-token:{pat}@github.com/{SFT_REPO}.git"
    branch = run.state["results_branch"] + ("-fail" if run.state.get("result") == "fail" else "")
    coll = run.dir / "results"
    coll.mkdir(exist_ok=True)
    got = []
    r = subprocess.run(["git", "clone", "--depth", "1", "--branch", branch, url, str(coll / "r")],
                       capture_output=True, text=True, timeout=900)
    if r.returncode == 0:
        for f in (coll / "r" / "score_results").glob("*"):
            shutil.copy(f, coll / f.name)
            got.append(f.name)
    if not (coll / "preds.jsonl.gz").exists():
        parts = sorted(coll.glob("preds.jsonl.gz.part.*"))
        if parts:
            with open(coll / "preds.jsonl.gz", "wb") as w:
                for pt in parts:
                    w.write(pt.read_bytes())
            got.append("preds.jsonl.gz(reassembled from %d parts)" % len(parts))
    run.mark("collect", collected=got)
    log(f"collect: {got or 'nothing on remote (instance may still be running the job)'}")
    if run.state.get("result") != "ok" and not got:
        # Branch path yielded nothing: the instance's own log is the ONLY
        # remaining evidence and destroy erases it. Pull it via the API first.
        _pull_instance_logs(run)
    # DESTROY decision (I4): success+forensics, or any breach => destroy now.
    if run.state.get("result") in ("ok", "fail", "cost_breach", "cap_hit", "deadline",
                                   "wedge", "vanished"):
        _destroy(run, run.state.get("result", "unknown"))
        run.mark("destroy")
    if run.state.get("result") != "ok":
        raise SystemExit(f"ALERT: rescore run {run.state['run_id']} did not produce results "
                         f"({run.state.get('result')}); forensics in {coll}; instance destroyed. "
                         f"Import phase skipped -- NO data was modified (no loss).")
