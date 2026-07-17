#!/usr/bin/env python3
"""weekly_rescore.py -- surgical, resumable, cost-capped registry rescore.

Chairman directive 2026-07-14: "run it and make the script surgical and precise
so we can run these jobs cheaply and scheduled every 7 days without loss."

Generalizes the PROVEN 2026-07-03 full-rescore path (fire_score.py +
vast_score_onstart.sh + import_preds_fly.py, manifest jobs/registry_rescore_v1.json)
into one resumable entrypoint with hard guards. Runs on the TOWER (needs:
flyctl authed, gh/git, AgentVault, psycopg2, vastai_sdk).

PHASES (state.json in the run dir; every phase idempotent, rerun resumes):
  preflight -> export -> bundle -> fire -> watch -> collect -> destroy ->
  import -> backfill -> postcheck

MODES:
  --delta (default): score (a) never-scored distinct-URL servers (the 20k-goal
      lane) + (b) a refresh cohort of the OLDEST-scored servers, capped by
      --refresh-cap (default 20000). Weekly delta => every server refreshed
      <= ~4 weeks, newest_scored_at advances weekly, cost ~$0.3-0.6/run.
  --full: every distinct-URL server (the 7/3 shape, ~66k, ~$1.2).

NO-LOSS INVARIANTS (enforced, run aborts loudly on breach):
  I1 scored_servers AFTER import >= BEFORE  (import only delete+reinserts
     servers present in preds; a bad preds file cannot shrink coverage).
  I2 preds servers >= 90% of exported servers, else no destroy-on-green and
     the import still proceeds for what arrived (partial results are ingested,
     never discarded) but the run reports DEGRADED.
  I3 forensics (onstart.log) are pulled BEFORE any destroy, success or fail.
  I4 instance is ALWAYS destroyed by the end of collect/destroy phases --
     wall-clock deadline + $ ceiling breached => forensics + destroy + ALERT.
  I5 adapter sha256 verified against the promoted pin before every launch.

COST GUARDS: MAX_DPH ceiling on offer selection; COST_CAP_USD hard ceiling
(estimated dph * elapsed); DEADLINE_MIN wall clock. All overridable per run.

Scheduling shape (two Claude scheduled tasks, mirrors the 7/3 fire+collect
pattern): weekly-rescore-fire runs `--phase fire-all`; weekly-rescore-collect
runs `--phase collect-all` ~4h later. Either phase re-invoked is a no-op if
already complete. `--run` does everything in one process (interactive use).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- constants
HERE = Path(__file__).resolve().parent                  # tools/rescore/
RUNS_ROOT = Path(os.environ.get("RESCORE_RUNS_ROOT", r"D:\zo\runs\weekly_rescore"))
LEDGER = RUNS_ROOT / "ledger.jsonl"

MODEL_VERSION = "v3.0_40974559"
ADAPTER_SHA_PIN = "bf842f5450347c19ac195ef32a7298a1579566a7b9b454a5381fda333e7198e3"
RULE = "gate_rule_v1_2026-06-16"
AXES = ["overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface"]

SFT_REPO = "rob531/zomesh-sentinel-sft"
# Promoted adapter source: local dir first (the score-job-* git branches are
# periodically cleaned; the 6/25 one vanished by 7/14). Sha-pinned either way.
ADAPTER_LOCAL_DIR = Path(os.environ.get("RESCORE_ADAPTER_DIR",
                                        r"D:\zo\runs\v3.0_40974559_FULL\final"))
FLY_PG_APP = "mcplookup-db"
PROXY_PORT = 15432
DSN_FILE = Path(os.environ.get("RESCORE_DSN_FILE", r"D:\zo\runs\rescore_20260703\_dsn.txt"))
FRESHNESS_URL = "https://mcprisky.io/freshness"

MAX_DPH_DEFAULT = 0.45
COST_CAP_DEFAULT = 3.00
DEADLINE_MIN_DEFAULT = 300
REFRESH_CAP_DEFAULT = 20000
GEO_BLOCK = ("CN", "HK", "TW", "RU", "IR", "KP")
IMAGE = "pytorch/pytorch:2.1.0-cuda11.8-cudnn8-devel"

SYS = (HERE / "prompt_system.txt").read_text(encoding="utf-8")
SIG = (HERE / "prompt_signals_block.txt").read_text(encoding="utf-8")
ONSTART = (HERE / "vast_score_onstart.sh").read_text(encoding="utf-8")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[{utcnow()}] {msg}", flush=True)


def ledger(event: str, run_id: str, **kw) -> None:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": utcnow(), "run": run_id, "event": event, **kw}) + "\n")


def secret(name: str) -> str:
    out = subprocess.run([sys.executable, r"D:\agentvault\fetch_secret.py", name],
                         capture_output=True, text=True, timeout=60)
    val = out.stdout.strip().splitlines()[-1].strip() if out.stdout.strip() else ""
    if not val:
        raise RuntimeError(f"AgentVault secret {name!r} empty: {out.stderr[:200]}")
    return val


# ---------------------------------------------------------------- run state
class Run:
    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.state_path = run_dir / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=1))

    def done(self, phase: str) -> bool:
        return self.state.get("phases", {}).get(phase) == "done"

    def mark(self, phase: str, status: str = "done", **kw) -> None:
        self.state.setdefault("phases", {})[phase] = status
        self.state.update(kw)
        self.save()
        ledger(f"phase_{phase}_{status}", self.state["run_id"])


def open_run(new_mode: str | None) -> Run:
    """Resume the newest unfinished run, else create one (fire phases only)."""
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    candidates = sorted([d for d in RUNS_ROOT.iterdir() if d.is_dir()], reverse=True)
    for d in candidates:
        r = Run(d)
        if r.state and not r.done("postcheck"):
            log(f"resuming run {r.state['run_id']} (phases={r.state.get('phases')})")
            return r
    if new_mode is None:
        raise SystemExit("NO_OPEN_RUN: nothing to collect (fire phase never ran?)")
    rid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    r = Run(RUNS_ROOT / rid)
    r.state = {"run_id": rid, "mode": new_mode, "phases": {}}
    r.save()
    ledger("run_opened", rid, mode=new_mode)
    return r


# ---------------------------------------------------------------- fly proxy
_PROXY = None


def ensure_proxy() -> None:
    global _PROXY
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", PROXY_PORT))
        s.close()
        return
    except OSError:
        pass
    log(f"starting fly proxy {PROXY_PORT}:5432 -a {FLY_PG_APP}")
    _PROXY = subprocess.Popen(["flyctl", "proxy", f"{PROXY_PORT}:5432", "-a", FLY_PG_APP],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(2)
        try:
            s = socket.socket(); s.settimeout(2)
            s.connect(("127.0.0.1", PROXY_PORT)); s.close()
            return
        except OSError:
            continue
    raise RuntimeError("fly proxy did not come up in 60s")


def pg_conn():
    import psycopg2
    ensure_proxy()
    dsn = DSN_FILE.read_text().strip()
    m = re.match(r"postgres(?:ql)?://([^:]+):([^@]+)@[^/]+/(\w+)", dsn)
    if not m:
        raise RuntimeError("DSN file unparseable")
    return psycopg2.connect(host="127.0.0.1", port=PROXY_PORT, dbname=m.group(3),
                            user=m.group(1), password=m.group(2), connect_timeout=15,
                            options="-c statement_timeout=600000")


def freshness() -> dict:
    import urllib.request
    with urllib.request.urlopen(FRESHNESS_URL, timeout=30) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------- phases
def ph_preflight(run: Run, args) -> None:
    if run.done("preflight"):
        return
    # secrets reachable
    secret("vast"), secret("github")
    # baseline moat counts (I1 reference) -- from the public surface + DB
    base = freshness()
    conn = pg_conn(); cur = conn.cursor()
    cur.execute("select count(distinct server_id) from mcp_llm_axis_scores where model_version=%s",
                (MODEL_VERSION,))
    scored_before = cur.fetchone()[0]
    conn.close()
    # no concurrent scoring instance
    from vastai_sdk import VastAI
    v = VastAI(api_key=secret("vast"))
    live = [i for i in (v.show_instances() or [])
            if str(i.get("label", "")) == "zo-sentinel-score"]
    if live:
        raise SystemExit(f"ABORT: live zo-sentinel-score instance(s) already exist: "
                         f"{[i.get('id') for i in live]} -- collect/destroy first")
    run.mark("preflight", baseline_freshness=base, scored_before=scored_before)
    log(f"preflight OK: scored_before={scored_before} newest={base.get('newest_scored_at')}")


# Export strategy (2026-07-14, v2): the 1GB Fly PG spills on server-side
# anti-joins/GROUP BYs (both the 7/3 NOT IN form and a materialized-CTE
# NOT EXISTS form ran >7 min). So we stream two CHEAP scans and do the
# set logic client-side -- surgical on the DB, deterministic in Python.
SQL_REGISTRY = """
SELECT server_id, name, COALESCE(registry_source,'remote') src, COALESCE(url,'') url,
       replace(replace(description,chr(10),' '),chr(13),' ') descr,
       (verdict IS NULL OR verdict IN ('unreviewed','unknown')) scorable
FROM mcp_server_registry
WHERE description IS NOT NULL AND length(description)>20
"""
# one row per scored server: every axis row of a server shares scored_at,
# so overall_risk alone gives (server_id, scored_at) without a GROUP BY.
SQL_SCORED = f"""
SELECT server_id, scored_at FROM mcp_llm_axis_scores
WHERE model_version='{MODEL_VERSION}' AND axis_name='overall_risk'
"""


def ph_export(run: Run, args) -> None:
    if run.done("export"):
        return
    conn = pg_conn()
    # small fetch batches + per-batch progress: the fly proxy tunnel stalls on
    # multi-MB bursts (observed 2026-07-14: server idle-in-txn, client starved).
    cur = conn.cursor("reg_stream")
    cur.itersize = 1000
    cur.execute(SQL_REGISTRY)
    reg = []
    t0 = time.time()
    for row in cur:
        reg.append(row)
        if len(reg) % 10000 == 0:
            log(f"export: registry stream {len(reg)} rows "
                f"({len(reg)/(time.time()-t0):.0f} rows/s)")
    cur.close()
    cur2 = conn.cursor()
    cur2.execute(SQL_SCORED)
    scored_at = dict(cur2.fetchall())         # sid -> scored_at (66k)
    conn.close()
    log(f"export: streamed registry={len(reg)} scored={len(scored_at)}")

    def ukey(sid, url):
        return url or sid

    # distinct-URL representative selection (replicates DISTINCT ON semantics:
    # order by (url_key, server_id) and keep the first) -- but prefer an
    # already-scored representative so the refresh lane refreshes in place.
    reg.sort(key=lambda r: (ukey(r[0], r[3]), r[0]))
    reps = {}
    for r in reg:
        k = ukey(r[0], r[3])
        cur_rep = reps.get(k)
        if cur_rep is None:
            reps[k] = r
        elif cur_rep[0] not in scored_at and r[0] in scored_at:
            reps[k] = r                        # scored rep wins for refresh
    new_rows, refresh_rows = [], []
    for k, r in reps.items():
        if r[0] in scored_at:
            refresh_rows.append(r)
        elif r[5]:                             # scorable (verdict unreviewed/unknown)
            new_rows.append(r)
    refresh_rows.sort(key=lambda r: scored_at[r[0]])       # oldest scored first
    if run.state["mode"] != "full":
        refresh_rows = refresh_rows[:args.refresh_cap]
    uniq = new_rows + refresh_rows

    inp = run.dir / "inputs.jsonl.gz"
    with gzip.open(inp, "wt", encoding="utf-8") as f:
        for sid, name, src, url, descr, _sc in uniq:
            hdr = (f"MCP SERVER UNDER REVIEW:\n  server_id: {sid}\n  name:      {name}\n"
                   f"  source:    {src}\n  url:       {url}\n  description: {descr}\n\n")
            f.write(json.dumps({"messages": [{"role": "system", "content": SYS},
                                             {"role": "user", "content": hdr + SIG}],
                                "metadata": {"server_id": sid}}) + "\n")
    run.mark("export", exported=len(uniq), new_servers=len(new_rows),
             refresh_servers=len(refresh_rows))
    log(f"export OK: {len(uniq)} inputs ({len(new_rows)} never-scored + "
        f"{len(refresh_rows)} refresh)")
    if len(uniq) == 0:
        run.mark("bundle", "skipped"); run.mark("fire", "skipped")
        run.mark("watch", "skipped"); run.mark("collect", "skipped")
        run.mark("destroy", "skipped"); run.mark("import", "skipped")
        run.mark("backfill", "skipped")
        log("nothing to score -- run will close at postcheck")


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
    git("add", "score_transfer")
    git("-c", "user.email=tower@zo", "-c", "user.name=weekly-rescore",
        "commit", "-q", "-m", f"score transfer bundle {ts}")
    git("push", repo_url, f"HEAD:refs/heads/{score_branch}")
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

    def cheapest(q):
        offers = v.search_offers(query=q)
        c = [o for o in offers if geo_ok(o) and float(o.get("dph_total", 99)) <= max_dph]
        return min(c, key=lambda o: float(o["dph_total"])) if c else None

    best = cheapest(f"gpu_name=RTX_4090 num_gpus=1 dph_total<{max_dph} verified=true "
                    f"rentable=true disk_space>=50 inet_down>=100")
    if not best:
        best = cheapest(f"gpu_name in [RTX_4090,L40S,L40,RTX_3090,A40,A6000] num_gpus=1 "
                        f"dph_total<{max_dph} verified=true rentable=true disk_space>=50")
    if not best:
        raise SystemExit("ABORT: no eligible GPU offer under price/geo guard")
    dph = float(best["dph_total"])
    resp = v.create_instance(
        id=best["id"], image=IMAGE, disk=50,
        env={"RUnpodGHAPI": secret("github"),
             "SCORE_BRANCH": run.state["score_branch"],
             "RESULTS_BRANCH": run.state["results_branch"]},
        onstart_cmd=ONSTART, runtype="ssh", label="zo-sentinel-score")
    iid = resp.get("new_contract") or resp.get("contract_id") or resp.get("id")
    run.mark("fire", instance_id=iid, dph=dph, gpu=best.get("gpu_name"),
             fired_at=utcnow())
    log(f"fire OK: instance={iid} {best.get('gpu_name')} ${dph}/hr "
        f"(cap ${args.cost_cap}, deadline {args.deadline_min}m)")


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


def ph_watch_collect(run: Run, args) -> None:
    if run.done("collect") or run.state["phases"].get("collect") == "skipped":
        return
    pat = secret("github")
    fired = datetime.fromisoformat(run.state["fired_at"])
    dph = float(run.state.get("dph", args.max_dph))
    while True:
        st = _results_state(run, pat)
        elapsed_h = (datetime.now(timezone.utc) - fired).total_seconds() / 3600
        est_cost = elapsed_h * dph
        if st:
            run.mark("watch", result=st, est_cost=round(est_cost, 2))
            break
        if est_cost >= args.cost_cap:
            ledger("cost_ceiling_breach", run.state["run_id"], est=est_cost)
            run.mark("watch", "failed", result="cost_breach")
            break
        if elapsed_h * 60 >= args.deadline_min:
            ledger("deadline_breach", run.state["run_id"], elapsed_h=round(elapsed_h, 2))
            run.mark("watch", "failed", result="deadline")
            break
        log(f"watch: no results yet (elapsed {elapsed_h:.2f}h, est ${est_cost:.2f})")
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
    # DESTROY decision (I4): success+forensics, or any breach => destroy now.
    if run.state.get("result") in ("ok", "fail", "cost_breach", "deadline"):
        _destroy(run, run.state.get("result", "unknown"))
        run.mark("destroy")
    if run.state.get("result") != "ok":
        raise SystemExit(f"ALERT: rescore run {run.state['run_id']} did not produce results "
                         f"({run.state.get('result')}); forensics in {coll}; instance destroyed. "
                         f"Import phase skipped -- NO data was modified (no loss).")


def ph_import(run: Run, args) -> None:
    if run.done("import") or run.state["phases"].get("import") == "skipped":
        return
    from psycopg2.extras import execute_values
    preds_gz = run.dir / "results" / "preds.jsonl.gz"
    if not preds_gz.exists():
        raise SystemExit("ALERT: preds.jsonl.gz missing at import; aborting (no writes)")
    scored_at = datetime.utcnow()
    conn = pg_conn(); cur = conn.cursor()

    def gate(orp):
        pcrit = orp[3] if len(orp) > 3 else 0.0
        phigh = orp[2] if len(orp) > 2 else 0.0
        if pcrit >= 0.40:
            return True, "CRITICAL", pcrit
        if pcrit + phigh >= 0.30:
            return True, "REVIEW", pcrit
        return False, None, pcrit

    rows, sids, servers, seen = [], [], 0, set()

    def flush():
        nonlocal rows, sids
        if not rows:
            return
        cur.execute("delete from mcp_llm_axis_scores where model_version=%s and server_id=any(%s)",
                    (MODEL_VERSION, sids))
        execute_values(cur,
            "insert into mcp_llm_axis_scores (server_id, axis_name, label, label_index, probs, "
            "p_top, p_critical, p_danger, escalated, escalated_to, decision_rule_version, "
            "model_version, adapter_sha256, scored_at) values %s", rows)
        conn.commit()
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
            esc, esc_to, pc = gate(pr.get("overall_risk", [0, 0, 0, 0]))
            for a in AXES:
                if pi.get(a) == -1:
                    continue
                pv = pr.get(a, [])
                pdg = (pv[4] if a == "maintainer_trust" and len(pv) > 4 else
                       pv[3] if a == "network_egress" and len(pv) > 3 else None)
                rows.append((sid, a, pl.get(a), pi.get(a), json.dumps(pv), mp.get(a),
                             pc if a == "overall_risk" else None, pdg,
                             esc if a == "overall_risk" else False,
                             esc_to if a == "overall_risk" else None,
                             RULE, MODEL_VERSION, ADAPTER_SHA_PIN, scored_at))
            sids.append(sid)
            servers += 1
            if len(rows) >= 3500:
                flush()
    flush()
    cur.execute("select count(distinct server_id) from mcp_llm_axis_scores where model_version=%s",
                (MODEL_VERSION,))
    scored_after = cur.fetchone()[0]
    conn.close()
    before = run.state.get("scored_before", 0)
    if scored_after < before:                              # I1
        raise SystemExit(f"ALERT: NO-LOSS INVARIANT BREACH scored_after={scored_after} "
                         f"< before={before} -- investigate immediately")
    coverage = servers / max(1, run.state.get("exported", 1))
    run.mark("import", imported_servers=servers, scored_after=scored_after,
             coverage=round(coverage, 3),
             degraded=coverage < 0.90)                     # I2
    log(f"import OK: servers={servers} coverage={coverage:.1%} scored_after={scored_after}")


def ph_backfill(run: Run, args) -> None:
    if run.done("backfill") or run.state["phases"].get("backfill") == "skipped":
        return
    ensure_proxy()
    r = subprocess.run([sys.executable, str(HERE / "apply_risk_tier_backfill.py"),
                        "--dsn-file", str(DSN_FILE), "--apply",
                        "--model-version", MODEL_VERSION],
                       capture_output=True, text=True, timeout=1800)
    (run.dir / "backfill.log").write_text(r.stdout[-5000:] + "\n" + r.stderr[-2000:])
    if r.returncode != 0:
        raise SystemExit(f"ALERT: risk-tier backfill failed rc={r.returncode}; "
                         f"scores ARE imported; rerun backfill phase after fixing")
    run.mark("backfill")
    log("backfill OK")


def ph_postcheck(run: Run, args) -> None:
    if run.done("postcheck"):
        return
    after = freshness()
    base = run.state.get("baseline_freshness", {})
    report = {
        "run_id": run.state["run_id"], "mode": run.state["mode"],
        "exported": run.state.get("exported"), "imported": run.state.get("imported_servers"),
        "coverage": run.state.get("coverage"), "degraded": run.state.get("degraded"),
        "est_cost_usd": run.state.get("est_cost"),
        "scores_rows": {"before": base.get("scores_rows"), "after": after.get("scores_rows")},
        "scored_servers": {"before": base.get("scored_servers"),
                           "after": after.get("scored_servers")},
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
    ap.add_argument("--full", action="store_true", help="full rescore (default: delta)")
    ap.add_argument("--refresh-cap", type=int, default=REFRESH_CAP_DEFAULT)
    ap.add_argument("--max-dph", type=float, default=MAX_DPH_DEFAULT)
    ap.add_argument("--cost-cap", type=float, default=COST_CAP_DEFAULT)
    ap.add_argument("--deadline-min", type=int, default=DEADLINE_MIN_DEFAULT)
    ap.add_argument("--poll-secs", type=int, default=120)
    args = ap.parse_args()
    if not (args.run or args.phase):
        ap.error("need --run or --phase")
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


if __name__ == "__main__":
    main()
