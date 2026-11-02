# weekly_rescore_lib/preflight.py -- PART OF weekly_rescore.py, not a standalone module.
# Fly proxy setup, PostgreSQL connection, freshness checks, preflight phase.
# weekly_rescore.py runs this file via _weekly_rescore_lib_exec("preflight") INSIDE
# weekly_rescore's own namespace, at the exact position this code stood
# (original lines 514-823), so every name here is still
# weekly_rescore.<name> and __file__ is weekly_rescore.py.
# Do not import it directly; callers keep citing weekly_rescore.<name>.
# (chain rebase_godfiles S20, 2026-11-02; map: weekly_rescore_lib/README.md)
from __future__ import annotations


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
    # FU-151: hand flyctl the credential this project already mandates BEFORE
    # spawning it. Measured 2026-07-28: this shell's FIRST attempt died on
    # "fly proxy did not come up in 60s"; hydrating FLY_API_TOKEN from AgentVault
    # made the same binary bind on the next run, same minute, same config.yml.
    # FU-137 shipped this remedy into ONE caller and every other flyctl caller
    # kept reading the ambient credential -- so the outage recurred the next day
    # in a different lane. Never raises; a dead vault falls through to ambient.
    _hydrated, _hydrate_note = hydrate_fly_token()
    log("fly token: " + _hydrate_note)
    # FU-133: flyctl's stderr used to go to DEVNULL and the failure surfaced as a
    # bare "did not come up in 60s" -- which names the symptom and hides the cause.
    # On 2026-07-28 the cause was `Error: no access token available`, from flyctl's
    # OWN client-side 720h login timer ageing out at 730h29m; the token itself still
    # authenticated against api.fly.io perfectly well. Recovering that one line cost
    # a manual re-run of the exact command the harness had already run and discarded.
    #
    # stderr goes to a FILE, not a PIPE: on the success path the proxy lives for the
    # whole run (hours) and nobody drains it, so a pipe would eventually fill its
    # buffer and wedge flyctl itself -- trading a silent failure for a worse one.
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    err_path = RUNS_ROOT / "_flyctl_proxy.err"
    err_f = open(err_path, "w+", encoding="utf-8", errors="replace")
    _PROXY = subprocess.Popen(["flyctl", "proxy", f"{PROXY_PORT}:5432", "-a", FLY_PG_APP],
                              stdout=subprocess.DEVNULL, stderr=err_f)
    for _ in range(30):
        time.sleep(2)
        try:
            s = socket.socket(); s.settimeout(2)
            s.connect(("127.0.0.1", PROXY_PORT)); s.close()
            return
        except OSError:
            pass
        if _PROXY.poll() is not None:
            break            # it is already dead; waiting out the clock is theatre
    detail = ""
    try:
        err_f.flush()
        said = err_path.read_text(encoding="utf-8", errors="replace").strip()
        if said:
            detail = " -- flyctl said: " + said.splitlines()[-1]
    except OSError:
        pass
    rc = _PROXY.poll()
    if rc is not None:
        detail = f" (flyctl exited {rc}){detail}"
    raise RuntimeError(f"fly proxy did not come up in 60s{detail}")


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


def freshness(timeout: int | None = None, retries: int | None = None) -> dict:
    """Read the public freshness surface.

    FU-027: the endpoint's cold path is slow and intermittently unavailable. Raising
    the budget and retrying turns a deterministic failure on a cache miss back into
    an occasional one. Callers that MUST NOT die on this should use freshness_safe().
    """
    import urllib.request
    timeout = FRESHNESS_TIMEOUT if timeout is None else timeout
    retries = FRESHNESS_RETRIES if retries is None else retries
    last: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            t0 = time.time()
            with urllib.request.urlopen(FRESHNESS_URL, timeout=timeout) as r:
                payload = json.loads(r.read().decode())
            if attempt > 1:
                log(f"freshness OK on attempt {attempt} ({time.time() - t0:.1f}s)")
            return payload
        except Exception as e:                                   # noqa: BLE001
            last = e
            log(f"freshness attempt {attempt}/{retries} failed after "
                f"{time.time() - t0:.1f}s: {type(e).__name__}: {e}")
            if attempt < retries:
                time.sleep(FRESHNESS_BACKOFF)
    raise RuntimeError(f"freshness unreachable after {retries} attempt(s): {last}") from last


def freshness_safe() -> tuple[dict, str | None]:
    """freshness() that never raises. Returns (payload, error_string).

    FU-027/FU-056: a read-only observability call must never fail a run whose writes
    have already been committed. On 2026-07-19 an unguarded 30s read killed a rescore
    after import and backfill had both succeeded, leaving the run open and half the
    moat a wave behind for two days with no detector able to see it.
    """
    try:
        return freshness(), None
    except Exception as e:                                       # noqa: BLE001
        return {}, f"{type(e).__name__}: {e}"


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
SQL_REGISTRY_KEYS = """
SELECT server_id, COALESCE(url,'') url,
       (verdict IS NULL OR verdict IN ('unreviewed','unknown')) scorable
FROM mcp_server_registry
WHERE description IS NOT NULL AND length(description)>20
"""
SQL_DETAIL = """
SELECT server_id, name, COALESCE(registry_source,'remote') src, COALESCE(url,'') url,
       replace(replace(description,chr(10),' '),chr(13),' ') descr
FROM mcp_server_registry
WHERE server_id = ANY(%s)
"""
# one row per scored server: every axis row of a server shares scored_at,
# so overall_risk alone gives (server_id, scored_at) without a GROUP BY.
SQL_SCORED = f"""
SELECT server_id, scored_at FROM mcp_llm_axis_scores
WHERE model_version='{MODEL_VERSION}' AND axis_name='overall_risk'
"""

# Per-cohort trust audit (FU-093 / FU-108, added 2026-07-26). ONE aggregate
# over the whole moat -- the Fly box is 1-vCPU burstable, so this is
# deliberately a single GROUP BY returning a few hundred rows, never a query
# per cohort. Result set is tiny; the scan is the cost.
SQL_COHORT_TRUST = """
SELECT scored_at, axis_name, label, count(*)
FROM mcp_llm_axis_scores
WHERE model_version=%s
GROUP BY scored_at, axis_name, label
"""


def _read_changed_priority() -> set:
    """Return server_ids from the V06 lifecycle_changed priority queue.

    Reads CHANGED_QUEUE (changed_priority.jsonl); returns the set of server_ids
    whose `consumed` field is False (i.e. not yet rescored since the change).
    Returns an empty set on any error so this never blocks the export phase.
    """
    try:
        if not CHANGED_QUEUE.exists():
            return set()
        sids = set()
        for line in CHANGED_QUEUE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                if not e.get("consumed") and "server_id" in e:
                    sids.add(e["server_id"])
            except Exception:
                pass
        return sids
    except Exception:
        return set()


# Histogram of ONE server set (the residue a cap-cut would leave in a cohort).
# server_id is PK -> indexed; the set is at most one cohort's tail.
SQL_RESIDUE_HIST = """
SELECT axis_name, label, count(*)
FROM mcp_llm_axis_scores
WHERE model_version=%s AND server_id = ANY(%s)
GROUP BY axis_name, label
"""


def residue_verdict(conn, sids, model_version: str = MODEL_VERSION) -> str:
    """Verdict the SAME gate would give the servers `sids` if they were a cohort."""
    from score_validity import validate_run_from_histogram
    cur = conn.cursor()
    cur.execute(SQL_RESIDUE_HIST, (model_version, list(sids)))
    axes: dict = {}
    for axis, label, n in cur.fetchall():
        axes.setdefault(axis, {})[label] = int(n)
    cur.close()
    return validate_run_from_histogram(axes)["verdict"]


# A cap cut INSIDE a cohort leaves a residue that cohort_trust judges on its
# own. Measured 2026-10-04 (FU-175 class): the 10-03 wave at --refresh-cap
# 140000 took 116811 of the 117916-server 2026-07-27 cohort; the 1105 left
# behind -- the same rows that were VALID the day before as part of the whole
# -- came back DEGENERATE (too few on-ladder rows for the declared exception),
# so DISTRUSTED_REMAINING went 0 -> 1105 with no new score written. At the
# 20000 default against ~22k cohorts this recurs EVERY week: each wave heals
# last week's residue and manufactures the next. Bound the overshoot so the
# snap can never double a wave's cost; when the only way out is a residue the
# gate would condemn, say so loudly rather than silently leave it.
SNAP_MAX_OVERSHOOT = 0.25


def snap_refresh_to_cohort(refresh_rows, cap, scored_at, judge,
                           max_overshoot: float = SNAP_MAX_OVERSHOOT):
    """Return refresh_rows[:k] with k chosen so the cut does not strand a residue
    the gate would DISTRUST. `judge(sids) -> verdict` is the gate itself.

    Rules, in order:
      * cut falls on a cohort boundary (or cap >= len)      -> plain slice
      * residue of the cut cohort judged PASSING/INSUFFICIENT -> plain slice
        (INSUFFICIENT is unevaluated, not distrusted -- FU-175 keeps the buckets apart)
      * residue DISTRUSTED and residue <= max_overshoot*cap -> extend to the whole cohort
      * else retreat to the cohort's start, unless that empties the slice
        (FU-181: an unpadded wave aborts) -> plain slice + RESIDUE_WARNING
    Returns (rows, note) where note is a one-line reason for the log.
    """
    from score_validity import DEGENERATE, SCHEMA_VIOLATION
    n = len(refresh_rows)
    if cap >= n or cap <= 0:
        return refresh_rows[:cap], "snap: cap >= rows, no cut"
    cut = scored_at[refresh_rows[cap - 1][0]]
    if scored_at[refresh_rows[cap][0]] != cut:
        return refresh_rows[:cap], "snap: cut on cohort boundary"
    j = cap
    while j < n and scored_at[refresh_rows[j][0]] == cut:
        j += 1
    residue = [r[0] for r in refresh_rows[cap:j]]
    verdict = judge(residue)
    if verdict not in (DEGENERATE, SCHEMA_VIOLATION):
        return (refresh_rows[:cap],
                f"snap: residue {len(residue)} of cohort {cut} judged {verdict}, plain slice")
    if len(residue) <= int(max_overshoot * cap):
        return (refresh_rows[:j],
                f"snap: residue {len(residue)} of cohort {cut} would be {verdict} -> "
                f"EXTENDED cap {cap} -> {j} (whole cohort)")
    i = cap - 1
    while i >= 0 and scored_at[refresh_rows[i][0]] == cut:
        i -= 1
    if i + 1 == 0:
        return (refresh_rows[:cap],
                f"snap: RESIDUE_WARNING residue {len(residue)} of cohort {cut} would be "
                f"{verdict}; overshoot {j - cap} > {max_overshoot:.0%} of cap and retreating "
                f"empties the refresh lane (FU-181) -> plain slice, residue WILL be distrusted")
    return (refresh_rows[:i + 1],
            f"snap: residue {len(residue)} of cohort {cut} would be {verdict}; overshoot "
            f"{j - cap} > {max_overshoot:.0%} of cap -> RETREATED cap {cap} -> {i + 1}")


def cohort_trust(conn, model_version: str = MODEL_VERSION):
    """Which scored_at cohorts already in the moat are NOT trustworthy?

    Every scoring wave stamps ONE shared scored_at on every row it writes, so
    scored_at IS the cohort key. Hand each cohort's own label histogram to the
    SAME gate that guards the import (score_validity) and believe its verdict.

    DERIVED on every run, never a hardcoded list of bad dates: a date list
    would be correct for exactly as long as it took the next wave to go wrong,
    and would silently keep condemning cohorts that had since been rescored.

    Returns (distrusted_cohorts:set, verdicts:{scored_at -> verdict}).
    """
    from score_validity import (DEGENERATE, SCHEMA_VIOLATION,
                                validate_run_from_histogram)
    cur = conn.cursor()
    cur.execute(SQL_COHORT_TRUST, (model_version,))
    hist: dict = {}
    for cohort, axis, label, n in cur.fetchall():
        hist.setdefault(cohort, {}).setdefault(axis, {})[label] = int(n)
    cur.close()
    verdicts, distrusted = {}, set()
    for cohort, axes in hist.items():
        verdict = validate_run_from_histogram(axes)["verdict"]
        verdicts[cohort] = verdict
        if verdict in (DEGENERATE, SCHEMA_VIOLATION):
            distrusted.add(cohort)
    return distrusted, verdicts
