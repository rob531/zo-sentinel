#!/usr/bin/env python3
"""Registration-drift check: the DECLARED daemon set vs the RUNNING one.

WHY THIS EXISTS
    Every daemon on this host is declared in exactly one of two places --
    go.sh (the boot script) or watchdog.sh (the every-tick supervisor) -- and
    nothing has ever compared those declarations against `ps`. So a daemon that
    dies stays dead until a human happens to look, and the only signal is the
    absence of one, which is the hardest signal in the world to notice.

    This single check would have caught, on its first cycle:

        - the proposed_to_pending_promoter crash-loop      (10 days dark)
        - the four "silent lane" reports of 2026-08-28     (10+ days dark)
        - both tower-scheduler disappearances

    It was proposed twice and never built. Built 2026-08-28.

THE TWO FAILURE MODES IT IS BUILT AGAINST

  1. FALSE GREEN FROM SUBSTRING pgrep.
     `pgrep -f promoter` matches the daemon, the daemon_wrapper.sh that
     supervises it, a stale editor, and -- the one that actually bit -- the
     ad-hoc launcher scripts under /home/workspace/logs/, which carry the
     daemon's own name in their path. A dead daemon with a live launcher
     script reads as RUNNING.

     So this module does NOT substring-match. It resolves each declaration to a
     CANONICAL INSTALL PATH, resolves every running process's script argument
     to an absolute path, and compares those. A process whose script resolves
     under LAUNCHER_EXCLUDE_DIRS is never counted as the daemon, whatever it is
     called. The exclusion is logged, not silent, so a real daemon accidentally
     installed under logs/ shows up as a finding rather than vanishing.

  2. AN ALARM THAT GOES QUIET WITH WHAT IT WATCHES.
     Every run -- including the clean one -- stamps a heartbeat. A check that
     writes only on failure is indistinguishable, when it dies, from a check
     that is passing. That distinction is this repo's recurring repair and it
     applies to this file more than to most, because this file is the one
     whose silence means "everything is fine".

WHAT IT DOES ABOUT DRIFT
    Missing on ONE cycle is not an incident: go.sh staggers its launches, a
    daemon may be mid-restart, and paging on a single sample is how an alarm
    earns itself an off switch. Missing on TWO CONSECUTIVE cycles is an
    incident, and gets a GitHub issue -- one per lane, reused and updated on
    each subsequent cycle rather than reopened, so a lane down for a week is
    one issue with a growing comment trail, not seven issues.

COVERAGE -- the standing rule (state the lane and the fraction, list the MISSES)
    LANE: the host daemon lane. It reads go.sh and watchdog.sh, which are the
    only two surfaces on this box where a user daemon may be declared
    (supervisord-user.conf is platform-owned, regenerated from an env var at
    boot, and reads nothing from /home/workspace -- so a user daemon declared
    there does not survive a reboot and is out of scope by construction).

    It reports its own coverage FRACTION and the NAMES of what it cannot see on
    every run. It does not cover: GitHub Actions schedules, the tower's Claude
    Desktop scheduled tasks (a different machine, unreachable from here), or
    crontab entries -- and it says so, because a drift check that silently
    scoped itself to half the daemons is the schema-prm problem again.

    It also declares ITSELF, so if this daemon dies, the next tick of whatever
    still runs reports it missing exactly like any other lane.

STDLIB ONLY, DELIBERATELY.
    Audit finding B2: untracked files in the build workspace shadow the
    committed package, so the same check scores differently on the host than on
    clean main. This module imports nothing from the repo, so there is nothing
    to shadow. Do not add a project import here.

Usage:
    python3 tools/registration_drift_check.py                 # one cycle, report
    python3 tools/registration_drift_check.py --daemon        # loop forever
    python3 tools/registration_drift_check.py --json out.json
    python3 tools/registration_drift_check.py --no-issues     # never touch GitHub
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# --- where things live -----------------------------------------------------

WORKSPACE = Path(os.environ.get("ZO_WORKSPACE", "/home/workspace"))
MESH = Path(os.environ.get("ZO_MESH", WORKSPACE / "zo_mesh"))
SENTINEL = Path(os.environ.get("ZO_SENTINEL", WORKSPACE / "zo_sentinel"))
LOGS = Path(os.environ.get("ZO_LOGS", WORKSPACE / "logs"))

GO_SH = MESH / "go.sh"
WATCHDOG_SH = MESH / "watchdog.sh"

DRIFT_LOG = LOGS / "registration_drift.log"
HEARTBEAT = LOGS / "registration_drift_heartbeat.json"
STATE = LOGS / "registration_drift_state.json"

#: A process whose script resolves under any of these is a LAUNCHER, never the
#: daemon. This is the false-positive class that made `pgrep -f <name>` useless:
#: the ad-hoc restart/diagnostic scripts under logs/ carry the daemon's own name.
LAUNCHER_EXCLUDE_DIRS = (LOGS.resolve(),)

#: Processes that RUN daemons but are not daemons. daemon_wrapper.sh carries its
#: child's script path as an argument, so counting it would let a live wrapper
#: over a dead child read as RUNNING -- the exact false green this check exists
#: to stop.
SUPERVISOR_SCRIPTS = {"daemon_wrapper.sh", "go.sh", "watchdog.sh",
                      "write_service_wrapper.sh", "sentinel_janitor.sh"}

REPO = "rob531/zo-sentinel"
ISSUE_LABEL = "agent:code-zo"

#: Consecutive cycles a lane must be absent before it earns an issue. 1 sample
#: is a restart; 2 is an outage.
ISSUE_AFTER_CYCLES = 2

#: ...and the absence must also have LASTED this long. Cycles are counted per
#: invocation, and on 2026-09-04/05 two instances of this check ran at once
#: (watchdog.sh's bare launch + go.sh's daemon_wrapper launch) sharing ONE
#: state file: "cycle 1" and "cycle 2" were 4 seconds apart, inside a go.sh
#: boot where intent_engine_daemon simply had not started yet. Issue #4706 was
#: filed for a daemon that came up 20s later. A counter cannot tell two samples
#: from two intervals; a clock can. Sized so one process on the default tick
#: still needs its second sample, and two processes on the same tick still
#: cannot fire on the first.
ISSUE_AFTER_SECONDS = 600

#: How often to nudge an ALREADY-OPEN drift issue. At the 15-minute tick a lane
#: down for a week would otherwise generate 672 comments.
REISSUE_EVERY_N_CYCLES = 24

DEFAULT_INTERVAL = 900  # 15 min, the watchdog's own cadence

# --- surfaces this check does NOT see, named so the gap is never implied ----

UNCOVERED_SURFACES = [
    ("github-actions", "workflow schedules run on GitHub, not on this host"),
    ("tower-scheduled-tasks",
     "Claude Desktop tasks on rczompsentinel; tailnet reaches the host but "
     "every port is filtered, so they cannot be polled from here"),
    ("crontab", "crontab entries are not daemons; and no cron daemon runs on "
                "this host, so they are inert regardless -- see #4122"),
    ("supervisord-user.conf",
     "platform-owned, regenerated from an env var at boot; a user daemon "
     "declared there does not survive a reboot, so it is out of scope"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 1. THE DECLARED SET
# ---------------------------------------------------------------------------

def _expand(raw: str) -> str:
    """Resolve the handful of shell vars these two files actually use."""
    return (raw.replace("$MESH", str(MESH)).replace("${MESH}", str(MESH))
               .replace("$SENTINEL", str(SENTINEL)).replace("${SENTINEL}", str(SENTINEL))
               .replace("$LOGS", str(LOGS)).replace("${LOGS}", str(LOGS)))


def _read_array(text: str, name: str) -> list[str]:
    m = re.search(rf"^{name}=\((.*?)^\)", text, re.M | re.S)
    if not m:
        return []
    return [ln.strip() for ln in m.group(1).splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _join_continuations(text: str) -> str:
    """Fold `\\`-continued shell lines into one logical line before parsing.

    Found by this check reporting ITSELF one-sided: its own go.sh registration
    is written across three lines, and every regex here is line-oriented, so the
    declaration was invisible and the daemon read as watchdog-only. A parser
    that silently cannot see a legal declaration reports a daemon as
    unregistered when it is registered -- and, far worse, would report a daemon
    as absent-from-go.sh when it is there. Fold first, then match.
    """
    return re.sub(r"\\\n\s*", " ", text)


def declared_from_go(text: str) -> dict[str, dict]:
    """Daemons go.sh starts. Four shapes, all present in the live file."""
    text = _join_continuations(text)
    out: dict[str, dict] = {}

    def add(name, script, source, kind="daemon"):
        p = Path(_expand(script))
        out.setdefault(name, {"name": name, "script": str(p),
                              "declared_in": [], "kind": kind})
        if source not in out[name]["declared_in"]:
            out[name]["declared_in"].append(source)

    # (a) nohup bash $MESH/daemon_wrapper.sh <NAME> <SCRIPT>
    for nm, sc in re.findall(
            r"daemon_wrapper\.sh\s+([\w\"$\{\}]+)\s+(\S+\.py)", text):
        nm = nm.strip('"').strip()
        if nm.startswith("$"):
            continue                      # the array fan-outs, handled below
        add(nm, sc, "go.sh")

    # (b) nohup python3 <PATH>.py     (bare, no wrapper)
    for sc in re.findall(r"nohup\s+(?:env\s+\S+=\S+\s+)?python3?\s+(\S+\.py)", text):
        if "$" in sc and not sc.startswith(("$MESH", "$SENTINEL")):
            continue
        p = Path(_expand(sc))
        if "$" in str(p):
            continue                      # loop variable, handled in (d)
        add(p.stem, str(p), "go.sh")

    # (b2) the self-looping monitors: nohup bash -c "while true; do python3 X"
    #      A different launch shape for the same thing. Missing this made
    #      loop_watch and graph_refresh read as UNDECLARED while go.sh starts
    #      them on every boot -- a false finding, and false findings are how a
    #      check like this gets switched off.
    for body in re.findall(r'nohup\s+bash\s+-c\s+"([^"]+)"', text):
        for sc in re.findall(r"python3?\s+(\S+\.py)", _expand(body)):
            p2 = Path(sc)
            if "$" not in str(p2):
                add(p2.stem, str(p2), "go.sh:loop")

    # (c) the two arrays fanned out through daemon_wrapper.sh
    for sc in _read_array(text, "TRUST_PIPELINE"):
        add(sc[:-3] if sc.endswith(".py") else sc, str(SENTINEL / sc),
            "go.sh:TRUST_PIPELINE")
    for sc in _read_array(text, "MESH_DAEMONS"):
        add(sc[:-3] if sc.endswith(".py") else sc, str(MESH / sc),
            "go.sh:MESH_DAEMONS")

    # (d) the port-gated API fan-out: "<file>:<port>:<log>"
    for blk in re.findall(r'for _svc in ((?:\s*"[^"]+"\s*\\?\s*)+);', text):
        for ent in re.findall(r'"([^"]+)"', blk):
            parts = ent.split(":")
            if len(parts) == 3:
                add(parts[0], str(SENTINEL / f"{parts[0]}.py"),
                    "go.sh:_svc_fanout", kind="service")

    return out


def declared_from_watchdog(text: str) -> dict[str, dict]:
    """Daemons watchdog.sh supervises every tick."""
    text = _join_continuations(text)
    out: dict[str, dict] = {}

    def add(name, script, source, kind="daemon"):
        p = Path(_expand(script))
        out.setdefault(name, {"name": name, "script": str(p),
                              "declared_in": [], "kind": kind})
        if source not in out[name]["declared_in"]:
            out[name]["declared_in"].append(source)

    # _daemon <script> <log> <Name> "<cmd>"  -- the canonical path is in <cmd>
    for sc, _lg, nm, cmd in re.findall(
            r'^_daemon\s+(\S+)\s+(\S+)\s+(\S+)\s+"([^"]*)"', text, re.M):
        m = re.search(r"(\S+\.py)", _expand(cmd))
        if m:
            path = m.group(1)
        elif "-m " in cmd:
            path = f"python -m {cmd.split('-m ',1)[1].split()[0]}"
        else:
            path = _expand(sc)
        add(nm, path, "watchdog.sh")

    # _svc <script> <port> <Name>
    #
    # A SERVICE IS VERIFIED BY ITS PORT, NOT BY ps. `_svc write_service_wrapper.sh
    # 8772 WriteService` names a WRAPPER; the process that actually answers is
    # write_service.py. Asking ps about the wrapper reports the service missing
    # while it is serving -- and the wrapper is in SUPERVISOR_SCRIPTS precisely
    # so a live wrapper cannot vouch for a dead child. The port is the only
    # honest question for this kind.
    for sc, port, nm in re.findall(r"^_svc\s+(\S+)\s+(\d+)\s+(\S+)", text, re.M):
        base = MESH if (MESH / sc).exists() else SENTINEL
        e = out.setdefault(nm, {"name": nm, "script": str(base / sc),
                                "declared_in": [], "kind": "service"})
        e["kind"] = "service"
        e["port"] = int(port)
        if "watchdog.sh:_svc" not in e["declared_in"]:
            e["declared_in"].append("watchdog.sh:_svc")
        # The wrapper's child is declared BY the wrapper being declared.
        # Without this, write_service.py -- started by the declared
        # write_service_wrapper.sh on every boot -- reports as UNDECLARED.
        if sc.endswith("_wrapper.sh"):
            child = base / (sc[:-len("_wrapper.sh")] + ".py")
            if child.exists():
                e["wrapped_child"] = str(child.resolve())

    # _daemon_tp fan-out over watchdog's OWN copy of TRUST_PIPELINE
    for sc in _read_array(text, "TRUST_PIPELINE"):
        add(sc[:-3] if sc.endswith(".py") else sc, str(SENTINEL / sc),
            "watchdog.sh:TRUST_PIPELINE")

    # bare pgrep guards for daemons with no _daemon line (WorldAgent, IntentEngine)
    #
    # These are REGEX PATTERNS, not paths: 'python.*intent_engine_daemon.py'
    # yields no install path at all. Treating the fragment as a path invented
    # /home/workspace/intent_engine_daemon.py and reported a healthy daemon
    # missing. So a guard records only its BASENAME and is matched on that --
    # deliberately weaker than a path match, and marked as such, because a
    # weaker true answer beats a stronger false one.
    for pat in re.findall(r"pgrep -c -f '([^']+)'", text):
        m = re.search(r"([\w.-]+\.py)", pat)
        if m:
            base = m.group(1)
            e = out.setdefault(base[:-3], {"name": base[:-3], "script": base,
                                           "declared_in": [], "kind": "guard"})
            e["kind"] = "guard"
            e["basename"] = base
            if "watchdog.sh:pgrep_guard" not in e["declared_in"]:
                e["declared_in"].append("watchdog.sh:pgrep_guard")

    return out


def _key(script: str) -> str:
    """IDENTITY IS THE CANONICAL SCRIPT PATH, NOT THE NAME.

    go.sh calls it `goose_runner`; watchdog.sh calls it `GooseRunner`. Keying on
    the name made one daemon look like two, and then reported BOTH halves as
    one-sided -- 39 findings that were an artefact of the two files disagreeing
    about capitalisation. Only the path says what is actually being run.
    """
    if script.startswith("python -m "):
        return script.rstrip("'\"")
    try:
        return str(Path(script).resolve())
    except OSError:
        return script


def merge_declared(a: dict, b: dict) -> dict:
    """Merge on canonical path; keep every name either file used as an alias."""
    out: dict[str, dict] = {}
    for src in (a, b):
        for v in src.values():
            # A guard knows only a basename. Key it to the full path any other
            # surface declared for the same file, so go.sh's real install path
            # and watchdog's guard are recognised as ONE daemon.
            if v.get("kind") == "guard":
                k = next((kk for kk in out if Path(kk).name == v["basename"]),
                         v["basename"])
            else:
                k = _key(v["script"])
                for gk in [kk for kk in out
                           if out[kk].get("kind") == "guard"
                           and Path(k).name == out[kk].get("basename")]:
                    out[k] = out.pop(gk)
                    out[k]["script"] = k
            if k not in out:
                out[k] = {"name": v["name"], "script": k, "aliases": [],
                          "declared_in": [], "kind": v["kind"]}
            e = out[k]
            if v.get("port"):
                e["port"] = v["port"]
            if v.get("basename"):
                e.setdefault("basename", v["basename"])
            if v.get("wrapped_child"):
                e["wrapped_child"] = v["wrapped_child"]
            if v["name"] not in e["aliases"]:
                e["aliases"].append(v["name"])
            for d in v["declared_in"]:
                if d not in e["declared_in"]:
                    e["declared_in"].append(d)
            if v["kind"] == "service":
                e["kind"] = "service"
            elif v["kind"] == "daemon" and e["kind"] == "guard":
                e["kind"] = "daemon"
    for e in out.values():
        # Prefer the snake_case script-derived name; the CamelCase watchdog label
        # is kept as an alias so a human grepping either one finds it.
        e["name"] = sorted(e["aliases"], key=lambda n: (n[:1].isupper(), n))[0]
    return out


# ---------------------------------------------------------------------------
# 2. THE RUNNING SET  (canonical path, launchers excluded)
# ---------------------------------------------------------------------------

def _script_of(argv: str) -> str | None:
    """The script a command line actually executes, as an absolute path.

    Returns None for anything that is not running a script we could name --
    which is correct: an unnameable process cannot satisfy a declaration.
    """
    toks = argv.split()
    for t in toks:
        if t.endswith((".py", ".sh")):
            try:
                return str(Path(t).resolve())
            except OSError:
                return t
    if " -m " in argv:
        mod = argv.split(" -m ", 1)[1].split()[0]
        return f"python -m {mod}"
    return None


def running_processes() -> tuple[list[dict], list[dict]]:
    """(real daemon processes, processes excluded as launchers)."""
    try:
        raw = subprocess.run(["ps", "-eo", "pid=,args="],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return [], []

    live, excluded = [], []
    self_script = str(Path(__file__).resolve())
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, argv = line.partition(" ")
        argv = argv.strip()
        if not argv or argv.startswith("["):
            continue
        script = _script_of(argv)
        if not script:
            continue
        rec = {"pid": pid, "script": script, "argv": argv[:300]}

        # A SUPERVISOR IS NOT THE DAEMON, and this is the load-bearing
        # exclusion. `bash daemon_wrapper.sh <name> <script>` carries the
        # daemon's own script path as an argument, so a wrapper whose child has
        # died still matches that path exactly -- a live supervisor over a dead
        # daemon reading as RUNNING. That is the precise false green this whole
        # check exists to stop, so the wrapper is dropped before matching and
        # the drop is recorded, never silent.
        base = Path(script).name
        if base in SUPERVISOR_SCRIPTS:
            rec["why"] = f"{base} is a supervisor/boot script, not a daemon"
            excluded.append(rec)
            continue

        # THE FALSE-POSITIVE CLASS. Named, logged, never silently dropped.
        if not script.startswith("python -m "):
            try:
                sp = Path(script).resolve()
                if any(str(sp).startswith(str(d) + os.sep) for d in LAUNCHER_EXCLUDE_DIRS):
                    rec["why"] = f"script lives under {LOGS} -- launcher, not the daemon"
                    excluded.append(rec)
                    continue
            except OSError:
                pass

        if script == self_script:
            rec["self"] = True
        live.append(rec)
    return live, excluded


def _port_open(port: int) -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def match(decl: dict, live: list[dict]) -> list[dict]:
    """Processes satisfying one declaration. Exact canonical path, not substring."""
    if decl.get("kind") == "service" and decl.get("port"):
        # The port answering IS the evidence for a service. Fall through to the
        # path match only if it does not, so a service on a renamed script is
        # still caught.
        if _port_open(decl["port"]):
            return [{"pid": f"port:{decl['port']}", "script": decl["script"],
                     "argv": f"/health 200 on :{decl['port']}"}]
    if decl.get("kind") == "guard" or ("basename" in decl
                                       and not Path(decl["script"]).is_absolute()):
        base = decl.get("basename") or Path(decl["script"]).name
        return [p for p in live if Path(p["script"]).name == base]
    want = decl["script"]
    if want.startswith("python -m "):
        mod = want[len("python -m "):]
        return [p for p in live if p["script"] == want
                or f" -m {mod}" in p["argv"]]
    try:
        want_r = str(Path(want).resolve())
    except OSError:
        want_r = want
    return [p for p in live if p["script"] == want_r]


# ---------------------------------------------------------------------------
# 3. STATE, HEARTBEAT, ISSUES
# ---------------------------------------------------------------------------

def _seconds_since(iso: str | None) -> float:
    """Age of a stored ISO timestamp in seconds; 0.0 when unparseable, so a
    corrupt `since` DELAYS an issue rather than forging one."""
    if not iso:
        return 0.0
    try:
        then = datetime.fromisoformat(iso)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - then).total_seconds())
    except Exception:
        return 0.0


def earns_issue(rec: dict) -> bool:
    """An absence earns an issue only when BOTH the sample count and the wall
    clock say so. Two processes sampling 4s apart satisfy the count; only an
    outage satisfies the clock."""
    return (rec.get("cycles", 0) >= ISSUE_AFTER_CYCLES
            and _seconds_since(rec.get("since")) >= ISSUE_AFTER_SECONDS)


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"consecutive_missing": {}, "issues": {}}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=2, sort_keys=True))


def emit_log(lines: list[str]) -> None:
    DRIFT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with DRIFT_LOG.open("a") as fh:
        for ln in lines:
            fh.write(f"[{now_iso()}] {ln}\n")


def emit_heartbeat(report: dict) -> None:
    """Stamped on EVERY cycle, clean ones included.

    A check that writes only when it finds something is, the moment it dies,
    indistinguishable from a check that is finding nothing.
    """
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.write_text(json.dumps({
        "ts": report["ts"],
        "declared": report["counts"]["declared"],
        "running": report["counts"]["running"],
        "missing": report["counts"]["missing"],
        "undeclared": report["counts"]["undeclared"],
        "one_sided": report["counts"]["one_sided"],
        "coverage_fraction": report["coverage"]["fraction"],
        "missing_names": [m["name"] for m in report["missing"]],
        "healthy": report["counts"]["missing"] == 0,
    }, indent=2))


def emit_bus(report: dict) -> None:
    """Best-effort write to the mesh bus so the drift is queryable, not only
    greppable. Never raises: a dead bus must not take the check down with it."""
    payload = {
        "table": "mesh_memory",
        "rows": [{
            "agent_id": "registration_drift_check",
            "memory_type": "registration_drift",
            "content": json.dumps({
                "ts": report["ts"],
                "declared": report["counts"]["declared"],
                "missing": [m["name"] for m in report["missing"]],
                "one_sided": [d["name"] for d in report["one_sided"]],
                "coverage_fraction": report["coverage"]["fraction"],
            }),
            "importance": 0.8 if report["counts"]["missing"] else 0.4,
        }],
    }
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:8772/write",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def _gh(args: list[str]) -> tuple[int, str]:
    if not shutil.which("gh"):
        return 127, "gh not on PATH"
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
        return r.returncode, (r.stdout or r.stderr).strip()
    except Exception as exc:                                  # pragma: no cover
        return 1, str(exc)


def raise_issue(name: str, decl: dict, cycles: int, st: dict) -> str | None:
    """One issue per lane. Reused on every later cycle, never reopened."""
    existing = st["issues"].get(name)
    body = (
        f"`{name}` is declared but no process matches its canonical install "
        f"path, for **{cycles} consecutive cycles**.\n\n"
        f"| | |\n|---|---|\n"
        f"| canonical path | `{decl['script']}` |\n"
        f"| declared in | {', '.join(decl['declared_in'])} |\n"
        f"| consecutive cycles absent | {cycles} |\n"
        f"| first seen absent | {st['consecutive_missing'].get(name, {}).get('since', 'unknown')} |\n\n"
        "Matched on the resolved script path, not by `pgrep` substring, and "
        f"any process whose script lives under `{LOGS}` was excluded as a "
        "launcher — so this is not the launcher-script false positive.\n\n"
        "**Read the log tail before restarting.** A daemon that died on a real "
        "defect will die again on restart, and restarting it without knowing "
        "why is how a crash-loop stays invisible for ten days.\n\n"
        f"Raised by `tools/registration_drift_check.py`. Heartbeat: `{HEARTBEAT}`."
    )
    if existing:
        # DO NOT comment every cycle. At a 15-minute tick a lane down for a week
        # would be 672 comments, and an issue nobody can read is an issue nobody
        # reads. One nudge per ~6h of continued absence; the heartbeat and the
        # drift log carry the per-cycle detail for anyone who wants it.
        if cycles % REISSUE_EVERY_N_CYCLES:
            return existing
        _gh(["issue", "comment", str(existing), "--repo", REPO,
             "--body", f"Still absent — **{cycles} consecutive cycles** "
                       f"as of {now_iso()}."])
        return existing

    rc, out = _gh(["issue", "create", "--repo", REPO,
                   "--label", ISSUE_LABEL,
                   "--title", f"registration drift: {name} declared but not running "
                              f"({cycles} cycles)",
                   "--body", body])
    if rc != 0:
        return None
    num = out.rstrip("/").rsplit("/", 1)[-1]
    st["issues"][name] = num
    return num


def close_issue(name: str, st: dict) -> None:
    num = st["issues"].pop(name, None)
    if num:
        _gh(["issue", "close", str(num), "--repo", REPO,
             "--comment", f"`{name}` is running again as of {now_iso()}. "
                          "Closed by tools/registration_drift_check.py."])


# ---------------------------------------------------------------------------
# 4. ONE CYCLE
# ---------------------------------------------------------------------------

def run_cycle(allow_issues: bool = True) -> dict:
    go_text = GO_SH.read_text() if GO_SH.exists() else ""
    wd_text = WATCHDOG_SH.read_text() if WATCHDOG_SH.exists() else ""

    d_go = declared_from_go(go_text)
    d_wd = declared_from_watchdog(wd_text)
    declared = merge_declared(d_go, d_wd)

    live, excluded = running_processes()

    missing, present = [], []
    for _k, decl in sorted(declared.items(), key=lambda kv: kv[1]["name"]):
        hits = match(decl, live)
        (present if hits else missing).append(
            dict(decl, pids=[h["pid"] for h in hits]))

    # Declared in go.sh but NOT watched by watchdog.sh (or the reverse). This is
    # drift too, and it is the quieter kind: the daemon starts at boot and then
    # nothing ever restarts it, so it dies once and stays dead.
    one_sided = []
    for _k, decl in sorted(declared.items(), key=lambda kv: kv[1]["name"]):
        in_go = any(s.startswith("go.sh") for s in decl["declared_in"])
        in_wd = any(s.startswith("watchdog.sh") for s in decl["declared_in"])
        if in_go != in_wd:
            one_sided.append(dict(
                decl, started_at_boot=in_go, supervised=in_wd,
                consequence=("starts at boot, nothing restarts it" if in_go
                             else "supervised but never started at boot")))

    declared_paths = set(declared)
    declared_paths |= {d["wrapped_child"] for d in declared.values()
                       if d.get("wrapped_child")}
    undeclared = [p for p in live
                  if p["script"] not in declared_paths
                  and not p.get("self")
                  and (str(SENTINEL) in p["script"] or str(MESH) in p["script"])]

    total = len(declared)
    report = {
        "ts": now_iso(),
        "counts": {
            "declared": total,
            "running": len(present),
            "missing": len(missing),
            "undeclared": len(undeclared),
            "one_sided": len(one_sided),
            "launchers_excluded": len(excluded),
        },
        "coverage": {
            "lane": "host daemon lane (go.sh + watchdog.sh)",
            "declared_surfaces_read": ["go.sh", "watchdog.sh"],
            "fraction": 1.0 if total else 0.0,
            "fraction_note": (
                "1.00 OF THE HOST DAEMON LANE, which is not the same as 1.00 of "
                "everything that runs. go.sh and watchdog.sh are the only two "
                "surfaces on this box where a user daemon may be declared; the "
                "surfaces below are outside that lane and are NOT covered."),
            "not_covered": [{"surface": s, "why": w} for s, w in UNCOVERED_SURFACES],
        },
        "missing": missing,
        "present": present,
        "one_sided": one_sided,
        "undeclared": undeclared,
        "launchers_excluded": excluded,
    }

    # --- state, issues -----------------------------------------------------
    st = load_state()
    lines = []
    for m in missing:
        rec = st["consecutive_missing"].setdefault(
            m["name"], {"cycles": 0, "since": report["ts"]})
        rec["cycles"] += 1
        m["consecutive_cycles"] = rec["cycles"]
        lines.append(f"MISSING {m['name']} -- declared in "
                     f"{','.join(m['declared_in'])}, canonical {m['script']}, "
                     f"cycle {rec['cycles']}")
        m["absent_seconds"] = _seconds_since(rec.get("since"))
        if allow_issues and earns_issue(rec):
            num = raise_issue(m["name"], m, rec["cycles"], st)
            if num:
                m["issue"] = num
                lines.append(f"  -> issue #{num}")

    for name in list(st["consecutive_missing"]):
        if name not in {m["name"] for m in missing}:
            st["consecutive_missing"].pop(name, None)
            if allow_issues:
                close_issue(name, st)
            lines.append(f"RECOVERED {name} -- running again")

    for d in one_sided:
        lines.append(f"ONE-SIDED {d['name']} -- {d['consequence']} "
                     f"({','.join(d['declared_in'])})")
    for p in undeclared:
        lines.append(f"UNDECLARED {p['script']} pid {p['pid']} -- running, "
                     f"in neither go.sh nor watchdog.sh")

    if not lines:
        lines.append(f"clean -- {len(present)}/{total} declared daemons running")
    save_state(st)

    emit_log(lines)
    emit_heartbeat(report)
    emit_bus(report)
    return report


# ---------------------------------------------------------------------------

def render(r: dict) -> str:
    c, out = r["counts"], []
    out.append("=" * 72)
    out.append("REGISTRATION DRIFT -- declared daemons vs running processes")
    out.append("=" * 72)
    out.append(f"  declared {c['declared']}   running {c['running']}   "
               f"MISSING {c['missing']}   one-sided {c['one_sided']}   "
               f"undeclared {c['undeclared']}")
    out.append(f"  launcher processes excluded (not counted as daemons): "
               f"{c['launchers_excluded']}")
    out.append("")
    if r["missing"]:
        out.append("[1] MISSING -- declared but no process on the canonical path")
        for m in r["missing"]:
            out.append(f"    {m['name']:<42} cycle {m.get('consecutive_cycles', 1)}"
                       + (f"  -> issue #{m['issue']}" if m.get("issue") else ""))
            out.append(f"        {m['script']}")
            out.append(f"        declared in: {', '.join(m['declared_in'])}")
    else:
        out.append("[1] MISSING ...................... none")
    out.append("")
    if r["one_sided"]:
        out.append("[2] ONE-SIDED -- declared on one surface only")
        for d in r["one_sided"]:
            out.append(f"    {d['name']:<42} {d['consequence']}")
    else:
        out.append("[2] ONE-SIDED .................... none")
    out.append("")
    if r["undeclared"]:
        out.append("[3] UNDECLARED -- running, in neither file")
        for p in r["undeclared"]:
            out.append(f"    pid {p['pid']:<8} {p['script']}")
    else:
        out.append("[3] UNDECLARED ................... none")
    out.append("")
    cov = r["coverage"]
    out.append(f"[4] COVERAGE -- lane: {cov['lane']}")
    out.append(f"    {cov['fraction']:.2f} of the lane "
               f"({c['declared']} declared daemons, both surfaces read)")
    out.append(f"    {cov['fraction_note']}")
    out.append("    NOT COVERED (named, not implied):")
    for nc in cov["not_covered"]:
        out.append(f"      - {nc['surface']}: {nc['why']}")
    out.append("=" * 72)
    return "\n".join(out)


def status_line() -> str:
    """ONE line, for a phone: phantom counts AND whether every declared lane runs.

    Two questions that have always needed two commands. The lane half is scanned
    live (a second or two). The phantom half is read from the newest referent
    census on disk and is PRINTED WITH ITS AGE -- an old number that says how old
    it is can be judged; an old number presented as current cannot.
    """
    r = run_cycle(allow_issues=False)
    c = r["counts"]
    lanes = (f"lanes {c['running']}/{c['declared']} up"
             + (f" | MISSING {','.join(m['name'] for m in r['missing'])}"
                if r["missing"] else "")
             + (f" | {c['one_sided']} one-sided" if c["one_sided"] else ""))

    phantom = "phantoms UNKNOWN (no local referent census)"
    newest, newest_ts = None, 0.0
    for cand in (SENTINEL / "artifacts" / "referent_verify.json",
                 Path("/tmp/referent_verify.json")):
        try:
            if cand.exists() and cand.stat().st_mtime > newest_ts:
                newest, newest_ts = cand, cand.stat().st_mtime
        except OSError:
            pass
    if newest:
        try:
            j = json.loads(newest.read_text())
            age_h = (time.time() - newest_ts) / 3600.0
            tm = j.get("tables", {})
            cm = j.get("columns", {})
            n_t = len(tm.get("missing", {})) if isinstance(
                tm.get("missing"), dict) else tm.get("missing", "?")
            n_c = len(cm.get("missing", {})) if isinstance(
                cm.get("missing"), dict) else cm.get("missing", "?")
            phantom = (f"phantom tables {n_t} / columns {n_c} "
                       f"(census {age_h:.0f}h old)")
        except Exception:
            phantom = "phantoms UNREADABLE (census present but unparseable)"
    return f"{phantom} | {lanes}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daemon", action="store_true", help="loop forever")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--no-issues", action="store_true",
                    help="never create or close GitHub issues")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--status", action="store_true",
                    help="one line: phantom counts AND whether every declared "
                         "lane is running. Exits non-zero if any lane is down.")
    a = ap.parse_args()
    # daemon_wrapper.sh does NOT forward trailing arguments to the script it
    # supervises (go.sh says so in its own comment at 12.8, which is why
    # loop_watch and graph_refresh use a bare nohup loop instead). Rather than
    # add a third launch shape to a host that already has too many, the daemon
    # mode is also settable by environment, which the wrapper DOES pass through.
    if os.environ.get("ZO_DAEMON") == "1":
        a.daemon = True

    if a.status:
        line = status_line()
        print(line, flush=True)
        return 0 if "MISSING" not in line else 1

    while True:
        r = run_cycle(allow_issues=not a.no_issues)
        if a.json:
            a.json.write_text(json.dumps(r, indent=2))
        if not a.quiet:
            print(render(r), flush=True)
        if not a.daemon:
            return 1 if r["counts"]["missing"] else 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
