"""vast_jobs.py -- managed lifecycle for EVERY paid GPU job (vast.ai et al).

WHY (chairman 2026-07-02: "training jobs run on vastai and don't get managed
by the E2E"): the SFT repo's dispatch_vast_v3.sh proved the right lifecycle
(launch -> pipeline -> 14-check DESTROY_READY gate -> forensics-before-destroy
-> CLOSEOUT_ALERT/CHAIRMAN_SURFACE), but it is a ONE-OFF bash orchestrator in
a sibling repo: zo-sentinel's E2E system has no visibility -- no ledger of
runs, no CI-enforced gate logic, no audit that a paid instance was actually
destroyed. The 6/25 registry-scoring run was fully manual. This module
GENERALIZES the proven semantics into a managed, observable harness:

  MANIFEST (jobs/<name>.json)  -- declares the job: launch spec, cost caps,
      expected artifacts + machine-readable CHECKS (the generalized
      DESTROY_READY gate), forensics globs, destroy policy.
  LIFECYCLE  preflight -> launch -> watch (deadline + cost ceiling) ->
      collect (forensics ALWAYS, even on failure -- scp logs BEFORE any
      destroy) -> verify (every check green/red, no early exit) -> resolve
      (auto-destroy iff ALL green AND policy allows; otherwise
      CLOSEOUT_ALERT.txt and the instance is LEFT ALIVE for inspection --
      chairman Option B preserved as the default).
  LEDGER (zo_sentinel_state/vast_jobs/ledger.jsonl) -- one machine-readable
      row per lifecycle event; the audit and the E2E read THIS, so no run
      can be invisible.
  AUDIT (`audit` subcommand, cron/scheduled-task-able) -- cross-checks the
      ledger against live vast instances: any live instance without an open
      ledger run, or any run past deadline without a verdict, is surfaced
      as an ALERT (money burning = someone finds out within a day, not at
      invoice time).

STANDING OPS DIRECTIVES ENFORCED IN CODE (project instructions):
  - forensics before destroy (collect step is unconditional),
  - DESTROY_READY-style machine-readable verification before releasing a
    paid resource,
  - hard cost ceilings with halt-and-surface (never silently burn budget).

Vast SDK/API access is INJECTED (VastClient protocol) -- the whole state
machine is hermetically testable in CI with a fake client; the real client
(vastai SDK / REST) is only imported inside RealVastClient. API key:
~/.config/vastai/vast_api_key, then AgentVault (`vast`), then VAST_API_KEY.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

DEFAULT_STATE_DIR = "/home/workspace/zo_sentinel_state/vast_jobs"
DEFAULT_COST_CAP_USD = 5.00          # per-job hard ceiling unless manifest raises it
DEFAULT_MAX_DPH = 1.00               # $/hr ceiling on instance selection
DEFAULT_DEADLINE_MIN = 240           # wall-clock bail
POLL_SECS = 60


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_dir() -> Path:
    return Path(os.environ.get("ZO_VAST_STATE_DIR", DEFAULT_STATE_DIR))


# ---------------------------------------------------------------------------
# Ledger -- the observability spine. Append-only jsonl; every event recorded.
# ---------------------------------------------------------------------------

def ledger_path() -> Path:
    return state_dir() / "ledger.jsonl"


def ledger_append(event: str, run_id: str, **fields) -> dict:
    row = {"at": _utcnow(), "event": event, "run_id": run_id, **fields}
    p = ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return row


def ledger_rows() -> List[dict]:
    p = ledger_path()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

REQUIRED_MANIFEST_FIELDS = ("name", "launch", "checks")


def load_manifest(path) -> dict:
    m = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [f for f in REQUIRED_MANIFEST_FIELDS if f not in m]
    if missing:
        raise ValueError(f"manifest missing fields: {missing}")
    m.setdefault("cost_cap_usd", DEFAULT_COST_CAP_USD)
    m.setdefault("max_dph", DEFAULT_MAX_DPH)
    m.setdefault("deadline_min", DEFAULT_DEADLINE_MIN)
    m.setdefault("auto_destroy", False)   # chairman Option B default
    m.setdefault("forensics", ["/workspace/onstart.log", "/workspace/*.log"])
    if not isinstance(m["checks"], list) or not m["checks"]:
        raise ValueError("manifest.checks must be a non-empty list")
    for c in m["checks"]:
        if c.get("type") not in ("file_exists", "file_min_bytes", "json_field",
                                 "jsonl_min_rows", "rc_zero"):
            raise ValueError(f"unknown check type: {c.get('type')!r}")
    return m


# ---------------------------------------------------------------------------
# Verification -- the generalized DESTROY_READY gate. Every check runs
# (green/red, no early exit); verdict is machine-readable JSON.
# ---------------------------------------------------------------------------

def run_checks(manifest: dict, workdir: Path, pipeline_rc: int) -> dict:
    results = []
    for c in manifest["checks"]:
        name = c.get("name") or c["type"]
        ok, detail = False, ""
        try:
            if c["type"] == "rc_zero":
                ok = pipeline_rc == 0
                detail = f"rc={pipeline_rc}"
            elif c["type"] == "file_exists":
                ok = (workdir / c["path"]).is_file()
                detail = c["path"]
            elif c["type"] == "file_min_bytes":
                p = workdir / c["path"]
                ok = p.is_file() and p.stat().st_size >= int(c["min_bytes"])
                detail = f"{c['path']} >= {c['min_bytes']}b"
            elif c["type"] == "json_field":
                data = json.loads((workdir / c["path"]).read_text(encoding="utf-8"))
                cur = data
                for part in c["field"].split("."):
                    cur = cur[part]
                op, want = c.get("op", "eq"), c["value"]
                ok = (cur == want if op == "eq" else
                      float(cur) >= float(want) if op == "gte" else
                      float(cur) <= float(want))
                detail = f"{c['field']}={cur} {op} {want}"
            elif c["type"] == "jsonl_min_rows":
                n = sum(1 for _ in (workdir / c["path"]).open(encoding="utf-8"))
                ok = n >= int(c["min_rows"])
                detail = f"{c['path']} rows={n} >= {c['min_rows']}"
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
        results.append({"name": name, "ok": bool(ok), "detail": detail})
    verdict = "DESTROY_READY" if all(r["ok"] for r in results) else "GATE_FAIL"
    return {"verdict": verdict, "at": _utcnow(),
            "passed": sum(r["ok"] for r in results),
            "total": len(results), "checks": results}


# ---------------------------------------------------------------------------
# Vast client seam (injected; hermetic tests use a fake)
# ---------------------------------------------------------------------------

class VastApiError(RuntimeError):
    """The vast API answered with an ERROR ENVELOPE -- not an empty result.

    Exists because the `vastai` CLI reports API failures (401 invalid key,
    rate limits) as JSON on **stderr** while exiting **0** with an EMPTY
    stdout. Every layer below then coerced that unknown into a zero:
    _cli returned {}, list_instances turned a non-list into [], and audit()
    published `live_instances: 0 / alerts: [] / ok: true` -- byte-identical
    to a genuine all-clear on the paid-GPU leak audit (FU-192, 2026-07-30).
    Unknown is not zero.
    """


def api_error(text: str) -> str:
    """Return a message if `text` carries a vast API error envelope, else "".

    The envelope is a JSON object with a truthy `error`, e.g.
    {"error": true, "status_code": 401, "msg": "Invalid user key"}.
    Scanned line-by-line because the CLI also emits DEPRECATED notices.
    """
    for line in (text or "").splitlines():
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("error"):
            return "status_code={} msg={}".format(
                obj.get("status_code"), obj.get("msg") or obj.get("error"))
    return ""


class RealVastClient:
    """Thin wrapper over the vastai CLI (subprocess) -- present on the tower.
    Only constructed for live runs; never imported paths in tests."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or resolve_api_key()

    def _cli(self, *args) -> dict:
        cmd = ["vastai", *args, "--raw", "--api-key", self.api_key]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise RuntimeError(f"vastai {' '.join(args[:2])} rc={out.returncode}: "
                               f"{(out.stderr or out.stdout)[:300]}")
        # rc=0 does NOT mean the API succeeded: the CLI prints its error
        # envelope to stderr and still exits 0 (FU-192). Check BOTH streams
        # before treating empty stdout as an empty result.
        err = api_error(out.stderr) or api_error(out.stdout)
        if err:
            raise VastApiError(f"vastai {' '.join(args[:2])}: {err}")
        return json.loads(out.stdout) if out.stdout.strip() else {}

    def launch(self, launch_spec: dict) -> str:
        args = ["create", "instance", str(launch_spec["offer_id"])] if "offer_id" in launch_spec else None
        if args is None:
            raise RuntimeError("live launch requires launch.offer_id (pick via "
                               "`vastai search offers` with max_dph filter first)")
        for k in ("image", "disk"):
            if k in launch_spec:
                args += [f"--{k}", str(launch_spec[k])]
        if "onstart_cmd" in launch_spec:
            args += ["--onstart-cmd", launch_spec["onstart_cmd"]]
        res = self._cli(*args)
        return str(res.get("new_contract") or res.get("id"))

    def status(self, instance_id: str) -> dict:
        insts = self._cli("show", "instances")
        for i in (insts if isinstance(insts, list) else []):
            if str(i.get("id")) == str(instance_id):
                return {"state": i.get("actual_status", "unknown"),
                        "dph": float(i.get("dph_total") or 0),
                        "ssh_host": i.get("ssh_host"), "ssh_port": i.get("ssh_port")}
        return {"state": "gone", "dph": 0.0}

    def list_instances(self) -> List[dict]:
        # Carry label/start_date/status_msg: audit() judges wedge + burn on
        # them, and a field dropped HERE cannot be recovered downstream --
        # every caller then re-derives it with a raw API call of its own.
        insts = self._cli("show", "instances")
        if not isinstance(insts, list):
            # Refusing to report an unrecognised shape as "no instances":
            # that silent [] is what made a 401 look like an all-clear.
            raise VastApiError(
                f"`show instances` returned {type(insts).__name__}, not a "
                f"list -- shape unknown, refusing to call it zero instances")
        return [{"id": str(i.get("id")), "state": i.get("actual_status"),
                 "dph": float(i.get("dph_total") or 0),
                 "label": i.get("label"),
                 "start_date": i.get("start_date"),
                 "gpu": i.get("gpu_name"),
                 "status_msg": i.get("status_msg")}
                for i in insts]

    def destroy(self, instance_id: str) -> bool:
        # Previously `return True` unconditionally, so a destroy that 401'd
        # was ledgered as destroyed=True while the instance kept BILLING.
        # _cli now raises on the error envelope; an explicit success=False
        # in the payload is honoured too.
        res = self._cli("destroy", "instance", str(instance_id))
        if isinstance(res, dict) and res.get("success") is False:
            return False
        return True

    def scp_from(self, instance: dict, remote_glob: str, local_dir: Path) -> bool:
        local_dir.mkdir(parents=True, exist_ok=True)
        # List argv, no shell (bandit B602): the remote glob expands on the
        # REMOTE side inside scp's own remote invocation, quoted defensively.
        argv = ["scp", "-o", "StrictHostKeyChecking=no",
                "-P", str(instance["ssh_port"]),
                f"root@{instance['ssh_host']}:{shlex.quote(remote_glob)}",
                str(local_dir) + "/"]
        return subprocess.run(argv, timeout=600, check=False).returncode == 0  # noqa: S603


def resolve_api_key() -> str:
    p = Path.home() / ".config" / "vastai" / "vast_api_key"
    if p.is_file():
        return p.read_text(encoding="utf-8").strip()
    try:
        out = subprocess.run(["python", "D:/agentvault/fetch_secret.py", "vast"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    key = os.environ.get("VAST_API_KEY", "")
    if not key:
        raise RuntimeError("no vast API key (vast_api_key file / AgentVault / env)")
    return key


# ---------------------------------------------------------------------------
# The managed lifecycle
# ---------------------------------------------------------------------------

def run_job(manifest: dict, client, workdir: Path,
            run_pipeline: Optional[Callable[[dict, dict], int]] = None,
            sleep: Callable = time.sleep, now: Callable = time.time) -> dict:
    """Full managed run. Returns the final ledger summary row. NEVER destroys
    without forensics; NEVER leaves a run without a verdict in the ledger."""
    run_id = f"{manifest['name']}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    workdir.mkdir(parents=True, exist_ok=True)
    ledger_append("launch_requested", run_id, manifest=manifest["name"],
                  cost_cap_usd=manifest["cost_cap_usd"],
                  deadline_min=manifest["deadline_min"])

    instance_id = client.launch(manifest["launch"])
    started = now()
    ledger_append("launched", run_id, instance_id=instance_id)

    # ---- watch: deadline + cost ceiling -------------------------------------
    est_cost, pipeline_rc, halted = 0.0, None, ""
    inst: Dict[str, Any] = {}
    while True:
        inst = client.status(instance_id)
        elapsed_min = (now() - started) / 60.0
        est_cost = (elapsed_min / 60.0) * float(inst.get("dph") or manifest["max_dph"])
        if est_cost >= float(manifest["cost_cap_usd"]):
            halted = f"COST_CEILING ${est_cost:.2f} >= ${manifest['cost_cap_usd']:.2f}"
            break
        if elapsed_min >= float(manifest["deadline_min"]):
            halted = f"DEADLINE {elapsed_min:.0f}min >= {manifest['deadline_min']}min"
            break
        if inst.get("state") == "gone":
            halted = "INSTANCE_GONE"
            break
        if run_pipeline is not None:
            # synchronous pipeline mode (SSH-driven): run once, then verify.
            pipeline_rc = run_pipeline(manifest, inst)
            break
        if inst.get("state") in ("exited", "stopped"):
            pipeline_rc = 0   # onstart-driven job finished; checks decide truth
            break
        sleep(POLL_SECS)

    # ---- collect: forensics ALWAYS, before any destroy decision -------------
    forensics_ok = True
    for g in manifest.get("forensics", []):
        try:
            if not client.scp_from(inst, g, workdir / "forensics"):
                forensics_ok = False
        except Exception:
            forensics_ok = False
    for g in manifest.get("artifacts", []):
        try:
            client.scp_from(inst, g, workdir)
        except Exception:
            pass
    ledger_append("collected", run_id, forensics_ok=forensics_ok,
                  est_cost_usd=round(est_cost, 2), halted=halted or None)

    # ---- verify: the generalized DESTROY_READY gate --------------------------
    gate = run_checks(manifest, workdir, 1 if halted else (pipeline_rc or 0))
    (workdir / "gate_verdict.json").write_text(json.dumps(gate, indent=2),
                                               encoding="utf-8")
    ledger_append("gate", run_id, verdict=gate["verdict"],
                  passed=gate["passed"], total=gate["total"])

    # ---- resolve: Option B destroy policy ------------------------------------
    destroyed = False
    if gate["verdict"] == "DESTROY_READY" and manifest.get("auto_destroy"):
        destroyed = bool(client.destroy(instance_id))
    if gate["verdict"] != "DESTROY_READY":
        (workdir / "CLOSEOUT_ALERT.txt").write_text(
            f"run_id={run_id}\ninstance_id={instance_id}\nhalted={halted}\n"
            f"gate={gate['passed']}/{gate['total']}\n"
            f"Instance LEFT ALIVE for inspection. Destroy with:\n"
            f"  python tools/vast_job_runner.py destroy {instance_id}\n",
            encoding="utf-8")
    summary = ledger_append(
        "resolved", run_id, instance_id=instance_id, verdict=gate["verdict"],
        destroyed=destroyed, est_cost_usd=round(est_cost, 2),
        instance_left_alive=not destroyed)
    return summary


# ---------------------------------------------------------------------------
# Audit -- the E2E hook: no run invisible, no instance unaccounted for.
# ---------------------------------------------------------------------------

# Chairman ruling 2026-07-17 (wedge scar): a paid instance still "loading"
# past this many minutes is burning money on nothing. Encoded HERE rather
# than in each caller's prose so every consumer applies the same threshold.
WEDGE_LOADING_MIN = 90


def destroy_cmd(instance_id) -> str:
    """The command a human fires AFTER pulling forensics. audit() never runs
    it -- forensics-before-destroy is a human/CofC gate, not an automation."""
    return f"python tools/vast_job_runner.py destroy {instance_id}"


def instance_facts(inst: dict, now_ts: Optional[float] = None) -> dict:
    """Flatten one live instance into the facts an audit must judge on:
    identity, label, price, how long it has been burning and what that has
    cost so far.

    `start_date` is a unix epoch from vast. If it is missing or unparseable
    the uptime is None, NEVER 0 -- a 0 reads as "just started" and would
    silently suppress the wedge guard, which is the failure mode this
    function exists to prevent.
    """
    now_ts = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
    dph = float(inst.get("dph") or 0)
    try:
        start = float(inst.get("start_date"))
        uptime_min = round((now_ts - start) / 60.0, 1)
        started_at = datetime.fromtimestamp(start, timezone.utc).isoformat()
    except (TypeError, ValueError):
        uptime_min, started_at = None, None
    return {
        "instance_id": str(inst.get("id")),
        "state": inst.get("state"),
        "label": inst.get("label"),
        "gpu": inst.get("gpu"),
        "dph": dph,
        "started_at": started_at,
        "uptime_min": uptime_min,
        "cost_so_far_usd": (round(dph * uptime_min / 60.0, 3)
                            if uptime_min is not None else None),
        "wedged": bool(inst.get("state") == "loading"
                       and uptime_min is not None
                       and uptime_min > WEDGE_LOADING_MIN),
    }


def audit(client, max_age_min: int = 24 * 60) -> dict:
    """Cross-check ledger vs live instances. ALERT classes:
    - orphan_instance: live vast instance with NO open (unresolved) ledger run
    - unresolved_run:  ledger run launched > max_age_min ago with no 'resolved'
    - alive_after_ready: run resolved DESTROY_READY but instance still alive
    - wedged_instance:  still "loading" past WEDGE_LOADING_MIN (paying for
      nothing) -- surfaced with a destroy_cmd, NEVER auto-destroyed

    Every alert carries the full instance_facts() payload (label, state,
    uptime_min, cost_so_far_usd) so the reader can tell a legitimate score
    wave from a leak without a second API call, and report["live"] lists
    every live instance whether or not the ledger knows about it.
    """
    rows = ledger_rows()
    launched = {r["run_id"]: r for r in rows if r["event"] == "launched"}
    resolved = {r["run_id"]: r for r in rows if r["event"] == "resolved"}
    open_instances = {str(launched[rid].get("instance_id"))
                      for rid in launched if rid not in resolved}
    ready_instances = {str(r.get("instance_id")): r for r in resolved.values()
                       if r.get("verdict") == "DESTROY_READY" and not r.get("destroyed")}
    alerts = []
    live = client.list_instances()
    # Report EVERY live instance, ledgered or not: ScoreWave/sprint200k and
    # moat-rescore launches write no ledger row (split-brain, 2026-07), so a
    # ledger-only view of paid compute is structurally incomplete.
    live_facts = [instance_facts(i) for i in live]
    for facts in live_facts:
        iid = facts["instance_id"]
        if iid not in open_instances and iid not in ready_instances:
            alerts.append({"class": "orphan_instance", **facts,
                           "destroy_cmd": destroy_cmd(iid)})
        if iid in ready_instances:
            alerts.append({"class": "alive_after_ready", **facts,
                           "run_id": ready_instances[iid]["run_id"],
                           "destroy_cmd": destroy_cmd(iid)})
        if facts["wedged"]:
            alerts.append({"class": "wedged_instance", **facts,
                           "wedge_threshold_min": WEDGE_LOADING_MIN,
                           "destroy_cmd": destroy_cmd(iid)})
    cutoff = datetime.now(timezone.utc).timestamp() - max_age_min * 60
    for rid, row in launched.items():
        if rid in resolved:
            continue
        try:
            at = datetime.fromisoformat(row["at"]).timestamp()
        except Exception:
            continue
        if at < cutoff:
            alerts.append({"class": "unresolved_run", "run_id": rid,
                           "instance_id": row.get("instance_id")})
    report = {"at": _utcnow(), "live_instances": len(live),
              "open_runs": len(launched) - len(resolved),
              "live": live_facts,
              "burn_dph_total": round(sum(f["dph"] for f in live_facts), 4),
              "alerts": alerts, "ok": not alerts}
    out = state_dir() / "last_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
