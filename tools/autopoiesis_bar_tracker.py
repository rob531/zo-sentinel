#!/usr/bin/env python3
"""Autopoiesis bar tracker -- the loop's daily measurement of its own progress.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT

    `autopoiesis-bar-tracker` was a Claude Desktop scheduled task on the tower
    (working directory `D:\\zo\\...`). Its outputs, however, land HERE: every
    input it measures is on this box, and it appends to
    /home/workspace/autopoiesis_bar.csv on this box. When the tower's scheduled-
    task surface stopped on 2026-07-27 the task went with it, and the CSV --
    the loop's own measure of whether any of this work is helping -- stopped on
    2026-08-13. It had been dark ever since.

    The tower cannot be reached from here (tailnet answers, every port filtered),
    so the task cannot be restarted where it lived. But the MEASUREMENT does not
    need the tower. This module is the mechanical half of that task, running
    locally, under the same supervision as every other daemon.

    IT IS THE NUMBERS, NOT THE JUDGEMENT. The original task also wrote a graded
    T1/T2/T3 scoreboard and a prose diagnosis of the binding constraint; that
    needs a model and a reading of the day's events, and this daemon does not
    fake it. Rows it writes carry `phase=MEASURED-ONLY` and an empty
    `actions_taken`, so nobody can mistake a machine row for a graded one. The
    series continues; the grading is a separate restoration.

    Writing a plausible grade would be worse than leaving it blank: this CSV is
    the instrument the whole loop is judged by, and an instrument that invents
    its readings is the failure mode this repo keeps finding.

WHAT IT MEASURES (all local, all mechanical)
    merge state, emission uptake, FU-031 degradation, spineful yield, census

IDEMPOTENT BY DAY. One row per date. A second run on the same day REPLACES that
day's row rather than appending a duplicate, so a restart does not corrupt the
series and a re-run after a fix records the corrected number.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.environ.get("ZO_WORKSPACE", "/home/workspace"))
SENTINEL = Path(os.environ.get("ZO_SENTINEL", WORKSPACE / "zo_sentinel"))
LOGS = Path(os.environ.get("ZO_LOGS", WORKSPACE / "logs"))
CSV_PATH = Path(os.environ.get("ZO_BAR_CSV", WORKSPACE / "autopoiesis_bar.csv"))
HEARTBEAT = LOGS / "autopoiesis_bar_heartbeat.json"

COLUMNS = ["date", "phase", "expanded_total", "redirects_total",
           "build_service_directives", "degradation_rate", "casing_repairs_24h",
           "staged_count", "active_count", "orphan_raw", "orphan_effective",
           "T1", "T2", "T3", "actions_taken"]

DEFAULT_INTERVAL = 86400  # daily, matching the original task's cadence


def sh(args, cwd=None, timeout=900) -> tuple[int, str]:
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as exc:
        return 1, str(exc)


def _count_dir(p: Path) -> int:
    if not p.is_dir():
        return 0
    return sum(1 for c in p.iterdir()
               if not c.name.startswith("__") and c.name != "__pycache__")


def measure() -> dict:
    m: dict[str, object] = {"date": datetime.now(timezone.utc).date().isoformat(),
                            "phase": "MEASURED-ONLY"}

    # 1. MERGE STATE -- read the tracked ref, never the dirty build workspace.
    sh(["git", "fetch", "-q", "origin", "main"], cwd=SENTINEL, timeout=180)
    _rc, head = sh(["git", "rev-parse", "--short", "origin/main"], cwd=SENTINEL)
    m["merge_head"] = head.strip()

    # 2. EMISSION UPTAKE
    prop = SENTINEL / "directives" / "proposed"
    m["expanded_total"] = len(list(prop.glob("*.expanded"))) if prop.is_dir() else 0
    red = prop / ".service_redirects.jsonl"
    m["redirects_total"] = (sum(1 for _ in red.open()) if red.exists() else 0)
    n = 0
    for d in ("proposed", "pending"):
        dd = SENTINEL / "directives" / d
        if dd.is_dir():
            n += sum(1 for f in dd.iterdir() if "build_service" in f.name)
    m["build_service_directives"] = n

    # 3. FU-031 degradation + autonomous casing heals
    rc, out = sh([sys.executable, "tools/builder_selftest_integrity_report.py",
                  "--since-hours", "24"], cwd=SENTINEL)
    mm = re.search(r"degradation[_ ]rate\D{0,20}?([\d.]+\s*%?)", out, re.I)
    m["degradation_rate"] = mm.group(1).strip() if mm else ("UNKNOWN"
                                                            if rc else "UNKNOWN")
    gl = LOGS / "goose_runner.log"
    m["casing_repairs_24h"] = "UNKNOWN"
    if gl.exists():
        try:
            tail = subprocess.run(["tail", "-n", "200000", str(gl)],
                                  capture_output=True, text=True,
                                  timeout=120).stdout
            m["casing_repairs_24h"] = tail.count("casing-repair")
        except Exception:
            pass

    # 4. SPINEFUL YIELD
    m["staged_count"] = _count_dir(SENTINEL / "services" / "staged")
    m["active_count"] = _count_dir(SENTINEL / "services" / "active")

    # 5. CENSUS
    m["orphan_raw"] = m["orphan_effective"] = "UNKNOWN"
    rc, out = sh([sys.executable, "tools/reachability_ratchet.py", "--quiet"],
                 cwd=SENTINEL, timeout=1200)
    art = SENTINEL / "artifacts" / "reachability_ratchet.json"
    if art.exists():
        try:
            j = json.loads(art.read_text())
            m["orphan_raw"] = j.get("orphan_count", "UNKNOWN")
            m["orphan_effective"] = j.get("effective_orphan_count", "UNKNOWN")
            m["ratchet_mode"] = j.get("mode", "UNKNOWN")
            m["deferred_declared"] = j.get("deferred_declared_count", "UNKNOWN")
        except Exception:
            pass
    if m["orphan_raw"] == "UNKNOWN":
        mm = re.search(r"orphans=(\d+)", out)
        if mm:
            m["orphan_raw"] = int(mm.group(1))
        mm = re.search(r"effective=(\d+)", out)
        if mm:
            m["orphan_effective"] = int(mm.group(1))

    # T1/T2/T3 are GRADES, not measurements. A machine row leaves them blank.
    m["T1"] = m["T2"] = m["T3"] = "NOT_GRADED"
    m["actions_taken"] = ""

    # PUBLISH THE BASIS WITH THE NUMBER (R5). The census is taken against the
    # BUILD WORKSPACE, which is what the original task measured -- it is the
    # loop's actual body. But that workspace carries untracked files that shadow
    # the committed package (#3943), so its orphan count runs HIGHER than a
    # clean checkout of the same commit. Recording the basis is what keeps the
    # series comparable; a number whose basis is unstated is the defect this
    # repo keeps re-finding.
    m["basis"] = f"build workspace {SENTINEL} @ origin/main {m['merge_head']}"
    return m


def write_row(m: dict) -> str:
    """One row per date; a same-day re-run replaces, never duplicates."""
    rows, header = [], COLUMNS
    if CSV_PATH.exists():
        with CSV_PATH.open(newline="") as fh:
            rd = list(csv.reader(fh))
        if rd:
            header = rd[0]
            rows = [r for r in rd[1:] if r and r[0] != m["date"]]
    row = [str(m.get(c, "")) for c in header]
    rows.append(row)
    rows.sort(key=lambda r: r[0])
    tmp = CSV_PATH.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    tmp.replace(CSV_PATH)
    return ",".join(row[:11])


def cycle() -> dict:
    m = measure()
    line = write_row(m)
    LOGS.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.write_text(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": m["date"], "row_written": True,
        "phase": m["phase"],
        "degradation_rate": m["degradation_rate"],
        "orphan_raw": m["orphan_raw"], "orphan_effective": m["orphan_effective"],
        "ratchet_mode": m.get("ratchet_mode"), "basis": m.get("basis"),
        "staged": m["staged_count"], "active": m["active_count"],
        "csv": str(CSV_PATH),
    }, indent=2))
    with (LOGS / "autopoiesis_bar_tracker.log").open("a") as fh:
        fh.write(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
                 f"row {line}\n")
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    a = ap.parse_args()
    # daemon_wrapper.sh does NOT forward trailing arguments to the script it
    # supervises (go.sh says so in its own comment at 12.8, which is why
    # loop_watch and graph_refresh use a bare nohup loop instead). Rather than
    # add a third launch shape to a host that already has too many, the daemon
    # mode is also settable by environment, which the wrapper DOES pass through.
    if os.environ.get("ZO_DAEMON") == "1":
        a.daemon = True
    while True:
        m = cycle()
        print(json.dumps(m, indent=2, default=str), flush=True)
        if not a.daemon:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
