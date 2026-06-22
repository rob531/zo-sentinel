#!/usr/bin/env python3
"""loop_watch.py -- end-to-end watcher for the self-improving loop:

    repo change  ->  graphify (code_nodes)  ->  memory (mesh_memory)  ->  directive (proposed/)  ->  goose cycle

It reads the latest signal at each stage and checks FLOW, not just liveness: if an
upstream stage is fresh but the next stage is stale past its threshold, the loop is
STALLED there -- and we localize exactly which hop broke and classify it with the
failure PLAYBOOK. Read-only (bus :8772 /query + local git + the proposed/ dir + the
architect log). Run on a schedule like pipeline-watch; pairs with a Cowork gauge.

    python3 loop_watch.py            # human summary + writes loop_watch_result.json
"""
import json, os, subprocess, sys, time, urllib.request
from datetime import datetime, timezone

BUS          = os.environ.get("ZO_WRITE_SERVICE", "http://127.0.0.1:8772") + "/query"
SENTINEL_DIR = os.environ.get("ZO_SENTINEL_DIR", "/home/workspace/zo_sentinel")
ARCH_LOG     = os.environ.get("ZO_ARCH_LOG", "/home/workspace/logs/sentinel_directive_generator_goose.log")
OUT          = os.environ.get("LW_OUT", "/home/workspace/zo_sentinel_state/loop_watch_result.json")

# downstream should follow upstream within N minutes, else the loop is STALLED there.
THRESH_MIN = {"memory":    int(os.environ.get("LW_MEM_MIN", 180)),
              "directive": int(os.environ.get("LW_DIR_MIN", 180)),
              "goose":     int(os.environ.get("LW_GOOSE_MIN", 30))}
LOOP_MEM_TYPES = ("directive_proposed", "build_artifact", "graph_change_observed")


# --- reads (best-effort; never raise) -----------------------------------------
def _bus(sql, timeout=8):
    try:
        req = urllib.request.Request(BUS, data=json.dumps({"sql": sql}).encode(),
                                     headers={"content-type": "application/json"}, method="POST")
        d = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace"))
        return d.get("rows", []) if isinstance(d, dict) else (d or [])
    except Exception:
        return []

def _git_head():
    try:
        return subprocess.run(["git", "-C", SENTINEL_DIR, "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""

def _read_goose_cycles(n=6):
    out = []
    try:
        with open(ARCH_LOG, encoding="utf-8", errors="replace") as f:
            for ln in f.readlines()[-500:]:
                if "goose TIMEOUT" in ln:
                    out.append({"rc": "timeout", "timeout": True})
                elif "goose returned rc=" in ln:
                    rc = ln.split("rc=", 1)[1].split(";", 1)[0].strip()
                    delta = ln.split("(+", 1)[1].split(")", 1)[0] if "(+" in ln else "?"
                    out.append({"rc": rc, "timeout": False, "delta": delta})
    except Exception:
        pass
    return out[-n:]

def read_signals(now=None):
    now = now or datetime.now(timezone.utc)
    g = _bus("SELECT built_at_commit AS c, COUNT(*) AS n FROM code_nodes "
             "GROUP BY built_at_commit ORDER BY n DESC LIMIT 1")
    types = ",".join("'%s'" % t for t in LOOP_MEM_TYPES)
    m = _bus(f"SELECT memory_type AS t, MAX(created_at) AS ts FROM mesh_memory "
             f"WHERE memory_type IN ({types}) GROUP BY memory_type")
    pdir = os.path.join(SENTINEL_DIR, "directives", "proposed")
    dts, dcount = None, 0
    try:
        fs = [os.path.join(pdir, f) for f in os.listdir(pdir)
              if f.endswith(".json") and not f.endswith((".done.json", ".failed.json"))]
        dcount = len(fs)
        if fs:
            dts = datetime.fromtimestamp(os.path.getmtime(max(fs, key=os.path.getmtime)),
                                         timezone.utc).isoformat()
    except Exception:
        pass
    return {"now": now.isoformat(), "repo_head": _git_head(),
            "graph_commit": (g[0]["c"] if g else "") or "",
            "memory": {r["t"]: r["ts"] for r in m},
            "proposed_newest": dts, "proposed_count": dcount,
            "goose": _read_goose_cycles()}


# --- assessment (PURE: signals dict -> verdict) -------------------------------
def _age_min(iso, now):
    if not iso:
        return None
    try:
        return (now - datetime.fromisoformat(str(iso).replace("Z", "+00:00"))).total_seconds() / 60.0
    except Exception:
        return None

def assess(sig):
    now = datetime.fromisoformat(sig["now"])
    st = {}
    # stage 1: is the graph behind the deployed repo? (commit-stamp compare)
    head, gc = sig.get("repo_head", ""), sig.get("graph_commit", "")
    graph_behind = bool(head and gc) and gc[:8] not in head and head[:8] not in gc
    st["graph"] = "stale" if graph_behind else "ok"
    # stage 2: memory writes keeping up?
    mem_ts = max([v for v in sig.get("memory", {}).values() if v], default=None)
    mem_age = _age_min(mem_ts, now)
    st["memory"] = "stale" if (mem_age is None or mem_age > THRESH_MIN["memory"]) else "ok"
    # stage 3: new directives being proposed? (this catches +0)
    dir_age = _age_min(sig.get("proposed_newest"), now)
    st["directive"] = "stale" if (dir_age is None or dir_age > THRESH_MIN["directive"]) else "ok"
    # stage 4: goose cycle hang -- recent cycles ALL timing out
    g = sig.get("goose", [])
    timeouts = [c for c in g if c.get("timeout")]
    st["goose"] = "hung" if (g and len(timeouts) == len(g)) else ("slow" if timeouts else "ok")
    # localize: first stage along the flow that isn't ok
    order = ["graph", "memory", "directive", "goose"]
    stall = next((s for s in order if st[s] != "ok"), None)
    overall = "ok" if stall is None else ("alert" if stall in ("graph", "goose") else "warn")
    return {"stages": st, "stall": stall, "overall": overall,
            "detail": {"graph_behind": graph_behind, "mem_age_min": mem_age,
                       "dir_age_min": dir_age, "proposed_count": sig.get("proposed_count"),
                       "goose_recent": g}}

# stall stage -> a hint into the failure PLAYBOOK (failure_classifier)
STALL_HINT = {
    "graph":     "graphify not refreshing -- run index_graph.py + load_graph_to_bus.py (no auto-trigger on merge yet).",
    "memory":    "graph fresh but no mesh_memory write -- the goose recipe/bridge isn't recording (check directive_mcp + shim).",
    "directive": "memory fresh but no new proposed/ -- architect +0 (model path: check shim 502s FIRST, then novelty). no_novel_builds.",
    "goose":     "goose cycles all TIMEOUT -- the 480s hang (weak model / heavy recipe / shim). capacity_429 or shim_5xx.",
}

def _write_heartbeat(verdict):
    """Watchdog-for-the-watcher: write a loop_heartbeat row each run via the SINGLE
    writer. If THIS stops appearing, the watcher itself died. Best-effort."""
    try:
        row = {"agent_id": "loop_watch", "memory_type": "loop_heartbeat",
               "content": json.dumps({"overall": verdict["overall"], "stall": verdict["stall"]}),
               "created_at": datetime.now(timezone.utc).isoformat()}
        urllib.request.urlopen(urllib.request.Request(
            BUS.replace("/query", "/write"),
            data=json.dumps({"table": "mesh_memory", "rows": [row], "wait": False}).encode(),
            headers={"content-type": "application/json"}, method="POST"), timeout=5)
    except Exception:
        pass


def loop_latency(sig):
    """Passive end-to-end latency proxy: how stale (minutes) the memory + directive
    signals are -- how long a change currently takes to traverse the loop. A synthetic
    canary without injecting anything destructive."""
    now = datetime.fromisoformat(sig["now"])
    mem_ts = max([v for v in sig.get("memory", {}).values() if v], default=None)
    return {"memory_age_min": _age_min(mem_ts, now),
            "directive_age_min": _age_min(sig.get("proposed_newest"), now)}


NOTIFY_URL = os.environ.get("ZO_NOTIFY_URL", "http://api.zo.computer/zo/notify")
NOTIFY_TO  = os.environ.get("ZO_NOTIFY_TO", "robin.craib@gmail.com")
STATE      = os.environ.get("LW_STATE", "/home/workspace/zo_sentinel_state/loop_watch_state.json")
RENOTIFY_H = int(os.environ.get("LW_RENOTIFY_H", 6))   # re-email an ongoing stall at most this often

def _load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_state(d):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "w") as f:
            json.dump(d, f)
    except Exception:
        pass

def email_report(result, force=False):
    """Email via the Zo notify channel (server-side, durable -- not a fragile artifact).
    Sends ONLY on ALERT (or force), de-duped: a NEW/changed stall emails immediately; an
    UNCHANGED ongoing stall re-emails at most every RENOTIFY_H hours. Returns True if sent."""
    overall, stall = result["overall"], result.get("stall")
    now = datetime.now(timezone.utc); st = _load_state(); send = force
    if overall == "alert":
        if stall != st.get("last_stall"):
            send = True
        else:
            last = st.get("last_alert_at")
            try:
                send = (not last) or (now - datetime.fromisoformat(last)).total_seconds() > RENOTIFY_H * 3600
            except Exception:
                send = True
    elif overall == "ok" and st.get("last_stall"):
        send, force = True, True   # one all-clear after a stall resolves
    if not send:
        return False
    subject = f"[zo-sentinel] loop {overall.upper()}" + (f" -- stall at {stall}" if stall else " -- all clear")
    body = (f"Self-improving loop watch  ({result['ran_at']})\n\n"
            f"OVERALL: {overall.upper()}    stall: {stall or 'none'}\n\n"
            + "\n".join(f"  {k:<10} {v}" for k, v in result["stages"].items())
            + f"\n\nhint: {result.get('hint','')}\n"
            f"repo={result.get('repo_head')}  graph={result.get('graph_commit')}\n"
            f"latency(min): {result.get('latency')}\n")
    try:
        import requests
        requests.post(NOTIFY_URL, json={"to": NOTIFY_TO, "subject": subject, "body": body[:2000]}, timeout=15)
        _save_state({"last_alert_at": now.isoformat(), "last_stall": stall if overall == "alert" else None})
        return True
    except Exception as e:
        print("email_report failed:", e)
        return False



def main():
    sig = read_signals()
    verdict = assess(sig)
    verdict["latency"] = loop_latency(sig)
    _write_heartbeat(verdict)
    result = {"watch": "loop_watch", "ran_at": sig["now"], **verdict,
              "repo_head": sig["repo_head"][:8], "graph_commit": sig["graph_commit"][:8],
              "hint": STALL_HINT.get(verdict["stall"], "loop flowing end-to-end.")}
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w") as f:
            json.dump(result, f, indent=2)
    except Exception:
        pass
    email_report(result)
    print(f"LOOP {result['overall'].upper()}  stall={result['stall']}")
    for s in ("graph", "memory", "directive", "goose"):
        print(f"  {s:10} {verdict['stages'][s]}")
    print("  hint:", result["hint"])
    return 0 if verdict["overall"] != "alert" else 2

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=0, help="daemon loop seconds (0 = one-shot)")
    args = ap.parse_args()
    if args.interval > 0:
        while True:
            try:
                main()
            except Exception as e:
                print("loop_watch cycle error:", e)
            time.sleep(args.interval)
    else:
        sys.exit(main())
