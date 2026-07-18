#!/usr/bin/env python3
"""wedge_guard.py -- boot-phase watchdog for weekly_rescore Vast instances.

Chairman policy 2026-07-17: instances stuck in actual_status=loading past
~15 min rarely recover. Poll every 2 min; on wedge: destroy, close the run
terminal, kill collect watchers, refire fire-all, relaunch collect watcher.
Max 3 refires then loud halt (WEDGE_GUARD_HALT.txt). Exits when instance
reaches running (collect watcher owns the rest of the lifecycle).
"""
import json, os, subprocess, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("RESCORE_RUNS_ROOT", r"D:\zo\runs\weekly_rescore"))
SCRIPT = os.environ.get("RESCORE_SCRIPT", str(Path(__file__).resolve().parent / "weekly_rescore.py"))
PYEXE = sys.executable
LOG = ROOT / "wedge_guard.log"
LEDGER = ROOT / "ledger.jsonl"
HALT = ROOT / "WEDGE_GUARD_HALT.txt"
WEDGE_MIN = 15
POLL_SECS = 120
MAX_REFIRES = 3
FIRE_ARGS = ["--phase", "fire-all", "--refresh-cap", "100000", "--cost-cap", "4", "--deadline-min", "720"]
COLLECT_ARGS = ["--phase", "collect-all", "--cost-cap", "4", "--deadline-min", "720"]

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def log(m):
    line = f"[{now()}] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f: f.write(line + "\n")
def ledger(event, run_id, **kw):
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now(), "run": run_id, "event": event, **kw}) + "\n")

def vast_key():
    return subprocess.run([PYEXE, r"D:\agentvault\fetch_secret.py", "vast"],
                          capture_output=True, text=True, timeout=60).stdout.strip()

def vast_api(method, path, key):
    req = urllib.request.Request("https://console.vast.ai/api/v0" + path,
                                 headers={"Authorization": "Bearer " + key}, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def newest_open_run():
    for d in sorted([p for p in ROOT.iterdir() if p.is_dir()], reverse=True):
        sp = d / "state.json"
        if sp.exists():
            s = json.loads(sp.read_text())
            if s.get("phases", {}).get("postcheck") != "done":
                return d, s
    return None, None

def close_run(run_dir, state, note):
    state["phases"].update({"watch": "failed", "collect": "skipped", "destroy": "done",
                            "import": "skipped", "backfill": "skipped", "postcheck": "done"})
    state["result"] = "wedged_loading"
    state["note"] = note
    (run_dir / "state.json").write_text(json.dumps(state, indent=1))

def kill_collect_watchers():
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object {$_.CommandLine -like '*weekly_rescore.py*'} | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60)

def spawn_detached(args, out_path):
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    out = open(out_path, "a")
    return subprocess.Popen([PYEXE, SCRIPT] + args, stdout=out, stderr=subprocess.STDOUT,
                            creationflags=flags)

def main():
    refires = 0
    log(f"wedge_guard start (WEDGE_MIN={WEDGE_MIN}m, MAX_REFIRES={MAX_REFIRES})")
    while True:
        try:
            key = vast_key()
            insts = vast_api("GET", "/instances/", key).get("instances", [])
        except Exception as e:
            log(f"api error: {e}; retrying"); time.sleep(POLL_SECS); continue
        run_dir, state = newest_open_run()
        if not insts:
            log("no live instances" + ("; no open run -- exiting" if not run_dir else "; open run but nothing live -- exiting (collect watcher will deadline it)"))
            return
        i = insts[0]
        iid, stat = i["id"], i.get("actual_status")
        el = (time.time() - i["start_date"]) / 60
        if stat == "running":
            log(f"{iid} RUNNING after {el:.1f}m -- guard done, collect watcher owns it"); return
        if stat != "running" and el >= WEDGE_MIN:
            if refires >= MAX_REFIRES:
                try: vast_api("DELETE", f"/instances/{iid}/", key)
                except Exception as e: log(f"destroy error: {e}")
                HALT.write_text(f"{now()} wedge_guard HALT: {MAX_REFIRES} refires exhausted; "
                                f"last instance {iid} destroyed; NO instance live; needs human/CEO session.\n")
                log("HALT: refires exhausted; destroyed last instance; wrote HALT file"); return
            log(f"{iid} WEDGED: {stat} {el:.1f}m >= {WEDGE_MIN}m -- destroying")
            try: vast_api("DELETE", f"/instances/{iid}/", key)
            except Exception as e: log(f"destroy error: {e}")
            mid = i.get("machine_id")
            if mid:
                bp = ROOT / "wedged_machines.json"
                cur = set(json.loads(bp.read_text())) if bp.exists() else set()
                cur.add(mid)
                bp.write_text(json.dumps(sorted(cur)))
                log(f"machine {mid} added to wedged_machines.json ({len(cur)} total)")
            rid = state.get("run_id", "?") if state else "?"
            burned = round(el / 60 * float((state or {}).get("dph", 0.33)), 2)
            ledger("wedge_guard_destroy", rid, instance=iid, machine=i.get("machine_id"), loading_min=round(el, 1), est_cost_usd=burned)
            if run_dir:
                close_run(run_dir, state, f"Instance {iid} wedged in loading {el:.1f}m; "
                          f"destroyed by wedge_guard at {now()}; run closed for refire.")
            kill_collect_watchers()
            log("refiring via fire-all (managed pipeline)")
            r = subprocess.run([PYEXE, SCRIPT] + FIRE_ARGS, capture_output=True, text=True, timeout=1800)
            tail = (r.stdout or "")[-500:] + (r.stderr or "")[-300:]
            log(f"fire-all rc={r.returncode} tail: {tail}")
            if r.returncode != 0:
                HALT.write_text(f"{now()} wedge_guard HALT: fire-all failed rc={r.returncode}\n{tail}\n")
                log("HALT: fire-all failed"); return
            nd, ns = newest_open_run()
            ledger("wedge_guard_refire", (ns or {}).get("run_id", "?"), instance=(ns or {}).get("instance_id"))
            spawn_detached(COLLECT_ARGS, ROOT / "collect_guard.log")
            refires += 1
            log(f"refire #{refires} done: run={(ns or {}).get('run_id')} instance={(ns or {}).get('instance_id')}; collect watcher relaunched")
        else:
            log(f"{iid} {stat} {el:.1f}m -- ok")
        time.sleep(POLL_SECS)

if __name__ == "__main__":
    main()


