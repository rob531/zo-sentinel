#!/usr/bin/env python3
"""sprint_import.py -- jsonl harvests -> prod mcp_server_registry.
Idempotent (ON CONFLICT DO NOTHING); rerun anytime. Fail-closed; unknown!=zero;
risk_tier always 'unassessed'; sids minted with the SAME per-lane functions the
container promoters use, so later daemon activity merges instead of forking."""
import atexit, json, glob, hashlib, os, re, socket, subprocess, sys, time, datetime
import psycopg2
from psycopg2.extras import execute_values

DIR = r"D:\zo\runs\sprint200k"
DSN_FILE, PORT, APP = r"D:\zo\runs\rescore_20260703\_dsn.txt", 15432, "mcplookup-db"
DRY = "--dry-run" in sys.argv

def log(m):
    line = "[%s] %s" % (datetime.datetime.utcnow().isoformat(), m)
    print(line)
    with open(DIR + r"\sprint_import.log","a",encoding="utf-8") as f: f.write(line+"\n")

VAULT_FETCH, VAULT_SERVICE = r"D:\agentvault\fetch_secret.py", "fly"

def hydrate_fly_token():
    """FU-149 (2026-07-29, discovery-harvest-daily): bare `flyctl` in this lane's
    scheduled-task shell has no access token -- the 720h interactive client timer
    lapsed. AgentVault holds a NON-EXPIRING `fly` org token (identity
    edd8edb7-...@tokens.fly.io) provisioned 2026-07-28 and then never wired in,
    which is why yesterday's import leg could not run on its own proxy at all.
    Read it via the sanctioned path and export it. Grants NO new authority: same
    credential, same app, same job -- it was simply never loaded. Never log the
    token itself, only its length."""
    if os.environ.get("FLY_API_TOKEN"):
        return True, "FLY_API_TOKEN already present in environment"
    if not os.path.exists(VAULT_FETCH):
        return False, "AgentVault fetch_secret.py absent at %s" % VAULT_FETCH
    try:
        r = subprocess.run([sys.executable, VAULT_FETCH, VAULT_SERVICE],
                           capture_output=True, text=True, timeout=90)
    except Exception as e:
        return False, "AgentVault fetch raised %s" % e
    tok = (r.stdout or "").strip()
    if r.returncode != 0 or not tok:
        return False, ("AgentVault fetch failed rc=%s %s"
                       % (r.returncode, (r.stderr or "").strip()[:200]))
    os.environ["FLY_API_TOKEN"] = tok
    return True, "FLY_API_TOKEN hydrated from AgentVault (len=%d)" % len(tok)


def tunnel_provenance(port=PORT):
    """FU-057 repair (2026-08-03, discovery-harvest-daily). The doctrine has said
    since 2026-07-28 that 'an inherited tunnel must be a VISIBLE DECISION', but the
    code made it invisible: if something is already listening on PORT the whole
    proxy block below is skipped, so the run logs NO 'fly auth: ... hydrated' line,
    NO proxy start, and nothing at all about whose tunnel it just used. Today's run
    inherited a 22h-old ORPHANED proxy (pid 13456, started 2026-08-02T13:10:53 by a
    parent that no longer exists) and was, in the log, indistinguishable from a run
    that made its own. That is the harness class exactly: SKIPPED IS NOT PASS -- the
    absence of the hydration line had been cited by prior runs as evidence the
    FU-149 path was healthy, when it only ever meant the check did not run.

    Returns (kind, detail) where kind is 'inherited' | 'own' -- never raises; a
    forensics helper must not be able to break the import it is describing."""
    try:
        s = socket.create_connection(("127.0.0.1", port), 1); s.close()
    except Exception:
        return "own", "port %d closed -- this run will start and own its proxy" % port
    detail = "port %d already LISTENING" % port
    try:
        # 2026-08-05: same port-table oracle as the reaper -- a blank CommandLine
        # used to make this log "listener is something else" about a flyctl proxy
        # sitting in plain sight on our port, which is R6 (unknown != zero) told
        # as if it were a finding.
        ps = subprocess.run(["powershell", "-NoProfile", "-Command",
            _proxy_probe(port)],
            capture_output=True, text=True, timeout=60)
        found = [l.strip() for l in (ps.stdout or "").splitlines() if l.strip()]
        mypid = os.getpid()
        for f in found:
            pid, ppid, created = (f.split("|") + ["", "", ""])[:3]
            owned = "OURS" if ppid.strip() == str(mypid) else "NOT-OURS(orphan-or-sibling)"
            detail += "; proxy pid=%s ppid=%s started=%s %s" % (pid, ppid, created, owned)
        if not found:
            detail += "; NO flyctl proxy process matches -- listener is something else"
    except Exception as e:
        detail += "; provenance probe failed: %s" % e
    return "inherited", detail


def _proxy_probe(port=PORT):
    """FU-057 repair (2026-08-05, discovery-harvest-daily). IDENTITY OF THE PROXY
    NOW COMES FROM THE PORT TABLE, NOT FROM A COMMAND-LINE STRING.

    The 2026-08-04 reaper identified the proxy solely by
    `CommandLine -match 'proxy\\s+<port>:'`. Today's orphan -- flyctl.exe pid 5256,
    ppid 26108 DEAD, 25.6h old, 0 established -- returned an EMPTY CommandLine
    under WMI, so that predicate matched ZERO rows against a listener the port
    table named plainly (verified live: CURRENT_PREDICATE_ROWS=0 vs
    PORT_OWNER_ROWS=1, owner pid=5256 name=flyctl.exe cmdline_len=0). The reaper
    would have logged a clean NO-OP, preflight would have short-circuited on the
    busy port, and this run would have INHERITED a fourth consecutive ownerless
    tunnel to prod PG with the FU-149 hydration path skipped -- the exact harm the
    reaper was built to end, defeated by a blank string. R1: resolve the running
    artifact from the RUNTIME. `Get-NetTCPConnection -LocalPort <port> -State
    Listen` -> OwningProcess IS the runtime oracle for "who holds our port"; a
    command line is a description of how a process was once asked to start, and it
    can be unreadable.

    Discovery is the UNION of (port owner) and (command-line match) -- strictly a
    superset of the old behaviour, so nothing that used to be found is lost. Both
    paths are still NAME-filtered to flyctl.exe/fly.exe, so a non-flyctl listener
    can never be selected. Widening DISCOVERY is safe precisely because the three
    kill conditions in reap_ownerless_tunnel() are what protect a sibling, not the
    matcher: parent ALIVE or any ESTABLISHED connection is still a logged NO-OP."""
    return (
        "$est=@(Get-NetTCPConnection -LocalPort %d -State Established "
        "-ErrorAction SilentlyContinue).Count; "
        "$pids=@(); "
        "$pids += @(Get-NetTCPConnection -LocalPort %d -State Listen "
        "-ErrorAction SilentlyContinue).OwningProcess; "
        "$pids += @(Get-CimInstance Win32_Process | Where-Object { "
        "($_.Name -eq 'flyctl.exe' -or $_.Name -eq 'fly.exe') -and "
        "$_.CommandLine -match 'proxy\\s+%d:' }).ProcessId; "
        "foreach($p in ($pids | Sort-Object -Unique)){ "
        "$o=Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId=' + $p) "
        "-ErrorAction SilentlyContinue; "
        "if($o -and ($o.Name -eq 'flyctl.exe' -or $o.Name -eq 'fly.exe')){ "
        "$par=@(Get-Process -Id $o.ParentProcessId "
        "-ErrorAction SilentlyContinue).Count; "
        "'{0}|{1}|{2}|{3}|{4}' -f $o.ProcessId,$o.ParentProcessId,$o.CreationDate,"
        "$(if($par -gt 0){'ALIVE'}else{'DEAD'}),$est } }"
    ) % (port, port, port)


def _pid_alive(pid):
    ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                         "@(Get-Process -Id %s -ErrorAction SilentlyContinue).Count" % pid],
                        capture_output=True, text=True, timeout=60)
    return (ps.stdout or "").strip() not in ("0", "")


def _wait_port_closed(port, seconds):
    for _ in range(int(seconds)):
        try:
            s = socket.create_connection(("127.0.0.1", port), 1); s.close()
        except Exception:
            return True
        time.sleep(1)
    return False


def _kill_and_verify(pid, seconds=12):
    """FU-057 repair (2026-08-05, discovery-harvest-daily). THE REAPER USED TO
    CLAIM A KILL IT NEVER CHECKED.

    Observed live today, not hypothesised: reap_ownerless_tunnel() logged
    `reaped: True -- REAPED ownerless orphan (pid=5256 ...)` and the very next
    line of the same script printed `15432 listening AFTER: True`, with
    Get-Process still returning pid 5256 and the port table still naming it as
    the owner. The old body fired Stop-Process, discarded the result, slept a
    flat 2s, checked NOTHING, and appended "REAPED" unconditionally -- so its
    success string was a statement of intent, not of outcome. The 2026-08-04
    behavioural test passed because a freshly-spawned test proxy dies inside 2s;
    a real 25h-old orphan did not, and no assertion had ever been SEEN RED for
    this branch (R4).

    That false success is worse than a failed reap: the caller believes the port
    is free, preflight then finds it BUSY, short-circuits the whole
    hydrate+own-proxy block, and the run inherits the very tunnel the reaper
    reported destroying -- FU-057 harm laundered through a green log line.

    So: kill, then POLL until the process is actually gone; escalate once to
    taskkill /F /T; and return False with the reason if it is still alive.
    Returns (gone, detail); never raises."""
    detail = []
    try:
        ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                             "Stop-Process -Id %s -Force" % pid],
                            capture_output=True, text=True, timeout=60)
        err = ((ps.stderr or "") + (ps.stdout or "")).strip()
        detail.append("Stop-Process rc=%s%s" % (ps.returncode,
                      (" err=" + err[:160]) if err else ""))
    except Exception as e:
        detail.append("Stop-Process raised: %s" % e)
    for _ in range(seconds):
        try:
            if not _pid_alive(pid):
                detail.append("confirmed gone")
                return True, "; ".join(detail)
        except Exception as e:
            detail.append("liveness probe failed: %s" % e); break
        time.sleep(1)
    detail.append("still alive after %ss, escalating to taskkill /F /T" % seconds)
    try:
        tk = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                            capture_output=True, text=True, timeout=60)
        detail.append("taskkill rc=%s" % tk.returncode)
    except Exception as e:
        detail.append("taskkill raised: %s" % e)
    for _ in range(6):
        try:
            if not _pid_alive(pid):
                detail.append("confirmed gone after taskkill")
                return True, "; ".join(detail)
        except Exception:
            break
        time.sleep(1)
    detail.append("STILL ALIVE -- reporting failure rather than a green log line")
    return False, "; ".join(detail)


def reap_ownerless_tunnel(port=PORT):
    """FU-057 repair (2026-08-04, discovery-harvest-daily). tunnel_provenance()
    made an inherited tunnel VISIBLE (2026-08-03) but it still only DESCRIBES one,
    so a human had to make the same judgement call by hand every single morning.
    That call has now come up three runs running: an ORPHANED flyctl proxy on OUR
    port, with a dead parent, was found on 2026-08-02 (pid 13456, 22h old), on
    2026-08-03 (reaped in-run) and again on 2026-08-04 (pid 21196, ppid 1824 gone,
    25h old, 0 established connections). None was started by this lane -- our runs
    land ~07:0x local and these appear mid-day -- so NOTHING ELSE WILL EVER CLOSE
    THEM. Each one silently hands the next run an ownerless tunnel to prod PG while
    skipping the FU-149 AgentVault hydration path outright, and skipped != pass.

    So: reap it, but ONLY when it is provably ownerless. ALL of these must hold --
      (a) the process is a flyctl/fly proxy bound to OUR port,
      (b) its parent process is GONE (nobody is waiting on it),
      (c) the port has ZERO ESTABLISHED connections (no in-flight query),
    -- because the 2026-07-29 lesson is the other half of this rule: a sibling
    lane's LIVE-parented proxy must be left strictly alone. Failing any condition
    is a NO-OP that logs WHY, never a kill. Reversible by construction: the caller
    falls through to the own branch, which hydrates from AgentVault and starts its
    own proxy (that branch was exercised green on 2026-08-04).

    Returns (reaped, detail); never raises -- a cleanup helper must not be able to
    break the import it exists to protect."""
    probe = _proxy_probe(port)
    try:
        ps = subprocess.run(["powershell", "-NoProfile", "-Command", probe],
                            capture_output=True, text=True, timeout=120)
        rows = [l.strip() for l in (ps.stdout or "").splitlines() if l.strip()]
    except Exception as e:
        return False, "probe failed, leaving tunnel alone: %s" % e
    if not rows:
        return False, "no flyctl proxy matches port %d -- listener is something else" % port
    mypid = str(os.getpid())
    reaped, notes = [], []
    for r in rows:
        pid, ppid, created, parent, est = (r.split("|") + ["", "", "", "", ""])[:5]
        tag = "pid=%s ppid=%s started=%s parent=%s established=%s" % (
            pid, ppid, created, parent, est)
        if ppid.strip() == mypid:
            notes.append("OURS, keeping (%s)" % tag); continue
        if parent.strip() != "DEAD":
            notes.append("parent ALIVE -- sibling may own it, left alone (%s)" % tag)
            continue
        if est.strip() not in ("0", ""):
            notes.append("has %s ESTABLISHED conn(s) -- in flight, left alone (%s)"
                         % (est.strip(), tag)); continue
        ok_kill, kill_detail = _kill_and_verify(pid)
        if ok_kill:
            reaped.append(pid)
            notes.append("REAPED ownerless orphan (%s) [%s]" % (tag, kill_detail))
        else:
            notes.append("reap ATTEMPTED BUT NOT CONFIRMED (%s) [%s]" % (tag, kill_detail))
    # 2026-08-05: the port is the outcome that matters -- a dead pid that has not
    # yet released the listener still makes preflight short-circuit and inherit.
    if reaped:
        released = _wait_port_closed(port, 15)
        notes.append("port %d released=%s" % (port, released))
        if not released:
            return False, ("; ".join(notes)
                           + "; NOT reporting success: pid killed but port still held")
    return bool(reaped), "; ".join(notes)

_OWN_PROXY_PID = None


def _proc_identity(pid):
    """(name, ppid) for pid from the WMI process table, ('', '') if gone.

    Deliberately NOT CommandLine-based: on 2026-08-05 a real 25h-old flyctl
    orphan returned an EMPTY CommandLine under WMI and defeated a matcher that
    trusted it. Name and ParentProcessId cannot come back blank for a live
    process, so ownership is decided on those two alone.
    """
    try:
        ps = subprocess.run(["powershell", "-NoProfile", "-Command",
            "$o = Get-CimInstance Win32_Process -Filter ('ProcessId=' + %s) "
            "-ErrorAction SilentlyContinue; "
            "if ($o) { $o.Name + '|' + $o.ParentProcessId }" % pid],
            capture_output=True, text=True, timeout=60)
        name, ppid = ((ps.stdout or "").strip().split("|") + ["", ""])[:2]
        return name.strip(), ppid.strip()
    except Exception:
        return "", ""


def _release_own_proxy(pid=None, port=PORT):
    """FU-057 repair (2026-08-06, discovery-harvest-daily). CLOSE THE TUNNEL WE
    OPENED, IN CODE, INSTEAD OF HOPING THE LANE READS STEP 4.

    Observed live this run, not hypothesised: main() ended at `con.close()` with
    NO atexit, NO finally and NO signal handler anywhere in the file (measured:
    0, 0, 0), so the proxy it spawns at line ~384 was left LISTENING on prod port
    15432 with its parent already exited -- flyctl pid 15712, ppid 31784 DEAD,
    holding an open tunnel to prod PG. It survived only because a human step in a
    SKILL happened to fire. That is the same shape as every other entry in this
    ledger: the safeguard existed as PROSE, and prose is what fails here. Two of
    the last four runs found an ownerless tunnel on this exact port; the
    preflight reaper added on 2026-08-04 cleans up yesterday's leak, but nothing
    prevented today's, and a lane cut mid-run by an MCP transport timeout (which
    happened TWICE to the 2026-08-06 run before the import even started) never
    reaches step 4 at all.

    OWNERSHIP IS RE-PROVEN AT KILL TIME, NOT ASSUMED FROM THE SPAWN.
    A pid recorded minutes ago may have died and been REUSED by an unrelated
    process, and killing on a stale pid is a far worse failure than leaking a
    tunnel. So all of these must hold, or this is a logged NO-OP:
      (a) we actually started a proxy this run (inherited tunnels are not ours),
      (b) the pid is still live and its process NAME is flyctl.exe/fly.exe,
      (c) its ParentProcessId is still THIS process.
    Condition (c) is what makes pid reuse safe: a recycled pid will not be
    parented to us.

    Then it reuses the ALREADY-HARDENED _kill_and_verify/_wait_port_closed rather
    than firing Stop-Process and declaring victory -- the 2026-08-05 lesson was
    that an unverified kill logged as success is worse than a failed one. An
    unconfirmed release says NOT CONFIRMED and returns False.

    Returns (released, detail); never raises. A cleanup hook that can throw
    during interpreter shutdown would turn a leaked tunnel into a crashed import.
    """
    pid = _OWN_PROXY_PID if pid is None else pid
    if not pid:
        return False, "no proxy started by this run (tunnel inherited or none needed) -- nothing to release"
    name, ppid = _proc_identity(pid)
    if not name:
        return True, "proxy pid=%s already gone -- nothing to release" % pid
    if name.lower() not in ("flyctl.exe", "fly.exe"):
        return False, ("REFUSED: pid=%s is now %r, not flyctl/fly -- pid reuse, leaving it alone"
                       % (pid, name))
    if ppid != str(os.getpid()):
        return False, ("REFUSED: pid=%s ppid=%s is not this process (%s) -- not ours, leaving it alone"
                       % (pid, ppid, os.getpid()))
    ok, detail = _kill_and_verify(pid)
    if not ok:
        return False, "self-reap NOT CONFIRMED (pid=%s) [%s]" % (pid, detail)
    released = _wait_port_closed(port, 15)
    detail += "; port %d released=%s" % (port, released)
    if not released:
        return False, ("self-reap NOT CONFIRMED (pid=%s): process gone but port still held [%s]"
                       % (pid, detail))
    return True, "released own proxy pid=%s [%s]" % (pid, detail)


def _atexit_release_own_proxy():
    try:
        ok, detail = _release_own_proxy()
        log("tunnel self-reap: %s -- %s" % ("RELEASED" if ok else "no-op/NOT CONFIRMED", detail))
    except Exception as e:
        try:
            log("tunnel self-reap raised (tunnel may still be open): %s" % e)
        except Exception:
            pass


def log_registry_composition(cur):
    """FU-054 continuity repair (2026-08-03). The runlog's free check is
    'prod before == yesterday's after'. It went RED today (before=470879 vs
    yesterday's after=470769, +110 this lane did not insert) and NOTHING on the box
    could attribute it: the foreign rows carry a BACKDATED first_seen, so they are
    invisible to any recent-first_seen query. Record per-registry_source counts each
    run so the next divergence is a one-line diff instead of a forensic dig."""
    try:
        cur.execute("SELECT registry_source, count(*) FROM mcp_server_registry GROUP BY 1 ORDER BY 2 DESC")
        log("registry composition: " + "; ".join("%s=%d" % (r[0], r[1]) for r in cur.fetchall()))
    except Exception as e:
        log("registry composition: UNAVAILABLE (%s) -- unknown != zero" % e)


def sid_github(full_name): return hashlib.md5(("github|%s" % full_name).encode()).hexdigest()[:16]
def sid_npm(name): return hashlib.md5(("npm|%s" % name).encode()).hexdigest()
def sid_pypi(name): return hashlib.blake2s(("pypi:%s" % name).encode("utf-8"), digest_size=8).hexdigest()

def rows_from_file(path):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    out, bad = [], 0
    for line in open(path, encoding="utf-8"):
        try: j = json.loads(line)
        except Exception: bad += 1; continue
        src = j.get("source")
        if src == "github":
            fn = j.get("full_name"); url = j.get("html_url")
            if not fn or not url: bad += 1; continue
            meta = {k: j.get(k) for k in ("stargazers_count","forks_count","open_issues_count",
                    "pushed_at","created_at","archived","disabled","fork","license_spdx",
                    "owner_login","owner_type","query")}
            meta["harvested_at"] = j.get("fetched_at"); meta["harvest_lane"] = "sprint_gh"
            out.append((sid_github(fn), fn, "github", url, j.get("description") or "", now, meta,
                    j.get("gh_repo_id")))
        elif src == "npm":
            name = j.get("name")
            if not name: bad += 1; continue
            url = "https://www.npmjs.com/package/%s" % name
            meta = {k: j.get(k) for k in ("repository","license","downloads","latest_release","keywords","via")}
            meta["harvested_at"] = j.get("fetched_at"); meta["harvest_lane"] = "sprint_npm"
            out.append((sid_npm(name), name, "npm", url, j.get("description") or "", now, meta, None))
        elif src == "pypi":
            name = j.get("name")
            if not name: bad += 1; continue
            url = "https://pypi.org/project/%s/" % name
            meta = {k: j.get(k) for k in ("repository","license","author","latest_release","keywords")}
            meta["harvested_at"] = j.get("fetched_at"); meta["harvest_lane"] = "sprint_pypi"
            out.append((sid_pypi(name), name, "pypi", url, j.get("description") or "", now, meta, None))
    return out, bad

def main():
    tuples, seen = [], set()
    for path in glob.glob(DIR + r"\*.jsonl"):
        rows, bad = rows_from_file(path)
        fresh = 0
        for r in rows:
            if r[0] in seen: continue
            seen.add(r[0]); tuples.append(r); fresh += 1
        log("%s: rows=%d fresh=%d fail_closed=%d" % (path.split("\\")[-1], len(rows), fresh, bad))
    prepared = [(sid, name, src, url, desc, None, "unknown", "", None, None, now, now, now, 1,
                 "unassessed", json.dumps(meta), ghid)
                for (sid, name, src, url, desc, now, meta, ghid) in tuples]
    log("prepared=%d" % len(prepared))
    if DRY or not prepared:
        log("dry-run or nothing to do"); return
    kind, detail = tunnel_provenance(PORT)
    log("tunnel: %s -- %s" % (kind, detail))
    if kind == "inherited":
        # FU-057 (2026-08-04): do not merely DESCRIBE an ownerless tunnel -- reap it
        # (strictly guarded; a live-parented sibling proxy is left alone) so this run
        # starts and owns its own, restoring the FU-149 AgentVault hydration path.
        reaped, why = reap_ownerless_tunnel(PORT)
        log("tunnel reap: %s -- %s" % ("REAPED" if reaped else "left alone", why))
        if reaped:
            kind, detail = tunnel_provenance(PORT)
            log("tunnel (after reap): %s -- %s" % (kind, detail))
    try:
        s = socket.create_connection(("127.0.0.1", PORT), 1); s.close()
    except Exception:
        # FU-057 repair (2026-07-28, discovery-harvest-daily): this used to kill
        # EVERY flyctl process, which also takes down the shared `flyctl agent run`
        # wireguard agent and any sibling task's in-flight `fly ssh`/sftp transfer
        # (observed today: a sibling's moat_preimport dump was mid-flight while this
        # lane was starting). Kill ONLY a stale proxy bound to OUR port.
        # 2026-08-05: port-table oracle here too (see _proxy_probe). This branch only
        # runs when the port is ALREADY CLOSED, so it is a belt-and-braces sweep for
        # a proxy that died without releasing -- still NAME-filtered to flyctl/fly so
        # the shared `fly agent run` wireguard agent can never be caught by it.
        subprocess.run(["powershell","-NoProfile","-Command",
            "$pids=@(); "
            "$pids += @(Get-NetTCPConnection -LocalPort %d -State Listen "
            "-ErrorAction SilentlyContinue).OwningProcess; "
            "$pids += @(Get-CimInstance Win32_Process | Where-Object { "
            "($_.Name -eq 'flyctl.exe' -or $_.Name -eq 'fly.exe') -and "
            "$_.CommandLine -match 'proxy\\s+%d:' }).ProcessId; "
            "foreach($p in ($pids | Sort-Object -Unique)){ "
            "$o=Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId=' + $p) "
            "-ErrorAction SilentlyContinue; "
            "if($o -and ($o.Name -eq 'flyctl.exe' -or $o.Name -eq 'fly.exe')){ "
            "Stop-Process -Id $o.ProcessId -Force } }" % (PORT, PORT)], capture_output=True)
        time.sleep(2)
        # FU-149 (2026-07-28): fail fast and name the real cause instead of spinning
        # 60s on a proxy that can never bind, then dying on an opaque psycopg2 error.
        # FU-149 (2026-07-29): load the AgentVault org token BEFORE asking
        # flyctl who it is -- otherwise this preflight aborts the entire
        # import on a credential that was available all along.
        ok_tok, msg_tok = hydrate_fly_token()
        log("fly auth: %s" % msg_tok)
        who = subprocess.run(["flyctl","auth","whoami"], capture_output=True, text=True)
        if who.returncode != 0:
            log("ABORT: flyctl auth unusable -- %s"
                % ((who.stderr or who.stdout).strip().replace("\n", " ")))
            raise SystemExit(2)
        # FU-057 (2026-08-06): KEEP the handle. The pid was discarded here, which
        # is why nothing in this file could ever close the tunnel it opened --
        # see _release_own_proxy(). Ownership is re-proven at kill time, so
        # recording the pid grants no licence to kill a recycled one.
        global _OWN_PROXY_PID
        _proxy_proc = subprocess.Popen(["flyctl","proxy","%d:5432"%PORT,"-a",APP],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _OWN_PROXY_PID = _proxy_proc.pid
        atexit.register(_atexit_release_own_proxy)
        log("proxy started pid=%d (own) -- self-reap armed at exit" % _OWN_PROXY_PID)
        for _ in range(60):
            try:
                s = socket.create_connection(("127.0.0.1", PORT), 1); s.close(); break
            except Exception: time.sleep(1)
    dsn = open(DSN_FILE).read().strip()
    dsn = re.sub(r"@[^/:@]+(:\d+)?/", "@127.0.0.1:%d/" % PORT, dsn)
    dsn = re.sub(r"host=\S+", "host=127.0.0.1", dsn); dsn = re.sub(r"port=\d+", "port=%d" % PORT, dsn)
    con = psycopg2.connect(dsn); cur = con.cursor()
    cur.execute("SET statement_timeout='300s'")
    cur.execute("SELECT count(*) FROM mcp_server_registry"); before = cur.fetchone()[0]
    log_registry_composition(cur)
    # FU-054 perf repair (2026-07-27, discovery-harvest-daily): the daily lane
    # globs every *.jsonl -- including the 253K-row full sweep -- and re-upserted
    # ALL ~382K prepared rows through the flyctl tunnel to land ~900 genuinely
    # new ones, holding the prod PG tunnel open for up to ~1h (FU-057 adjacency).
    # Anti-filter against the server_ids prod already has BEFORE inserting.
    # Semantics are unchanged: this only drops rows that ON CONFLICT DO NOTHING
    # would have discarded anyway, and the ON CONFLICT clause is RETAINED as the
    # race backstop. Wrapped in try/except so any failure falls back to the
    # original full-upsert path rather than skipping the import.
    try:
        t0 = time.time()
        cur.execute("SELECT server_id FROM mcp_server_registry")
        existing = set(r[0] for r in cur)
        n_before_filter = len(prepared)
        prepared = [p for p in prepared if p[0] not in existing]
        log("anti-filter: existing=%d prepared=%d to_insert=%d skipped=%d (%.1fs)"
            % (len(existing), n_before_filter, len(prepared),
               n_before_filter - len(prepared), time.time() - t0))
    except Exception as e:
        log("anti-filter FAILED (%s) -- falling back to full upsert" % e)
    cols = ("server_id,name,registry_source,url,description,trust_score,verdict,verdict_reasoning,"
            "confidence,last_assessed,first_seen,last_seen,last_scanned,scan_count,risk_tier,metadata,"
            "gh_repo_id")
    if prepared:
        execute_values(cur,
            "INSERT INTO mcp_server_registry (%s) VALUES %%s ON CONFLICT (server_id) DO NOTHING" % cols,
            prepared, page_size=5000)
        con.commit()
    else:
        log("nothing net-new to insert")
    cur.execute("SELECT count(*) FROM mcp_server_registry"); after = cur.fetchone()[0]
    log("prod before=%d after=%d net_new=%d" % (before, after, after - before))
    con.close()

if __name__ == "__main__": main()
