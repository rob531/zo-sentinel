# ruff: noqa: F821  -- names injected into globals at exec time by weekly_rescore.py
# weekly_rescore_lib/core.py -- PART OF weekly_rescore.py, not a standalone module.
# utilities, Run dataclass, run state tracking and lifecycle management.
# weekly_rescore.py runs this file via _weekly_rescore_lib_exec("core") INSIDE
# weekly_rescore's own namespace, at the exact position this code stood
# (original lines 129-509), so every name here is still
# weekly_rescore.<name> and __file__ is weekly_rescore.py.
# Do not import it directly; callers keep citing weekly_rescore.<name>.
# (chain rebase_godfiles S20, 2026-11-02; map: weekly_rescore_lib/README.md)
from __future__ import annotations


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
_DRIVER_PROV = None


def _driver_provenance() -> dict:
    """Which tree is this code running from? Computed once per process, report-only.

    `HERE` has been resolved at import since the first version of this file and has
    never once been written down, so no artifact a wave leaves behind says which
    worktree fired it. The cost of that omission is measurable: "which worktree does
    `moat-rescore-weekly` actually RUN from?" has been an open question since
    2026-08-11 and could only ever be answered by ELIMINATION -- on 2026-09-13 a
    census of 94 rescore trees on the tower could narrow it to 33 and no further.
    R1 asks every lane to resolve the running artifact from the RUNTIME rather than
    a repo path; this is the runtime writing its own answer down once, at the moment
    it matters, instead of thirty-three lanes guessing later.

    Same shape as #4814 (a status the watch loop already held and omitted from the
    line a human reads) and FU-358 -- a value measured, held, and never published.

    NEVER RAISES. A provenance helper that can kill a paid fire is strictly worse
    than no helper at all. Every field independently degrades to the string
    "UNKNOWN": a tree that is not a git checkout, a git that is missing, and a git
    that times out must all read UNKNOWN and never a plausible-looking default,
    because unknown is not zero (R6) and a fabricated sha is worse than a blank.
    """
    global _DRIVER_PROV
    if _DRIVER_PROV is not None:
        return dict(_DRIVER_PROV)
    prov = {"launch_dir": "UNKNOWN", "git_sha": "UNKNOWN", "dirty": "UNKNOWN",
            "driver_md5": "UNKNOWN", "host": "UNKNOWN"}
    try:
        import hashlib as _hashlib
        prov["launch_dir"] = str(HERE)
        prov["driver_md5"] = _hashlib.md5(Path(__file__).resolve().read_bytes()).hexdigest()
    except Exception:  # noqa: BLE001
        pass
    try:
        prov["host"] = socket.gethostname()
    except Exception:  # noqa: BLE001
        pass
    for key, argv, conv in (
        ("git_sha", ["git", "rev-parse", "HEAD"], lambda s: s.strip() or "UNKNOWN"),
        ("dirty", ["git", "status", "--porcelain"],
         lambda s: bool([ln for ln in s.splitlines() if ln and not ln.startswith("??")])),
    ):
        try:
            r = subprocess.run(argv, cwd=str(HERE), capture_output=True, text=True,
                               timeout=30)
            if r.returncode == 0:
                prov[key] = conv(r.stdout)
        except Exception:  # noqa: BLE001
            pass
    _DRIVER_PROV = prov
    return dict(prov)


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

    def _record_driver(self) -> None:
        """Stamp this PROCESS's driver into the run, deduped.

        `driver` is the most recent observation; `driver_seen` accumulates every
        distinct (launch_dir, git_sha) the run has been touched by. The two differ
        precisely when a run is fired from one tree and collected from another --
        which is the failure this exists to make visible, so recording only the
        first observation would hide the one case worth recording.
        """
        try:
            prov = _driver_provenance()
            self.state["driver"] = prov
            seen = self.state.setdefault("driver_seen", [])
            key = [prov.get("launch_dir"), prov.get("git_sha")]
            if key not in seen:
                seen.append(key)
        except Exception:  # noqa: BLE001
            pass

    def mark(self, phase: str, status: str = "done", **kw) -> None:
        self.state.setdefault("phases", {})[phase] = status
        self._record_driver()
        self.state.update(kw)
        self.save()
        ledger(f"phase_{phase}_{status}", self.state["run_id"])


def _terminally_finished(state: dict) -> bool:
    """Can this run EVER produce data again, however many times it is resumed?

    FU-321 (2026-08-11). `open_run` asked only "is postcheck done?", which has no
    answer for a run that FAILED. On 2026-08-11 run 20260811-061104 fired, the pod
    FATALed fetching the transfer bundle, the instance was destroyed and import was
    correctly skipped -- and the run then stayed, forever, "the newest unfinished
    run". Every subsequent `--run` resumed it, aborted at import with
    `preds.jsonl.gz missing`, and exited 1. Reproduced twice before it was found.

    That is a DAM, not a stall: the next scheduled Tuesday run would have done the
    same, spent $0, reported failure, and left the moat to go stale indefinitely --
    the exact silent-staleness failure this lane exists to catch, committed by the
    lane's own harness.

    The deeper defect was TWO INSTRUMENTS DISAGREEING ABOUT ONE WORD. `--check-open
    -runs` reads the LEDGER and honours an abort vocabulary (19 runs already sit in
    that bucket); `open_run` read only the FILESYSTEM phase map and never consulted
    it. A run could be simultaneously "deliberately closed, not stranded" and "the
    newest unfinished run". Both readings were defensible; nothing reconciled them.

    Terminal means the GPU is gone AND no predictions exist, so there is no path
    back. `result: "ok"` is deliberately absent: a successful run closes via the
    `run_closed` ledger event, and one that did not IS the stranded shape.
    """
    result = str((state or {}).get("result") or "").lower()
    if not result:
        return False                      # no verdict recorded = still resumable
    if result.startswith(_ABANDON_RESULT_PREFIXES):
        return True                       # killed_/abort_/abandon_/cancel_, spend released
    if result.startswith("ok"):
        return False                      # success closes via run_closed; if it did not,
                                          # that IS the stranded shape and must keep alarming
    # ANY other recorded verdict, once the instance is gone, is terminal. Deliberately NOT
    # an enumeration of the failures seen so far. The first version of this function listed
    # `fail`, and hours after it merged, wave 20260811-063956 returned `cost_breach` -- a
    # verdict this file already knows about three lines away, which matched nothing here,
    # dammed the pipeline identically, and proved that enumerating known failure names is
    # the same defect in a new costume. `deadline` would have been the third.
    # Every phase after `fire` needs an instance; without one the run cannot advance however
    # often it is resumed. `destroyed` is the load-bearing half: a run still HOLDING an
    # instance stays resumable whatever its state.json claims, because unreleased spend is
    # exactly what `--check-open-runs` exists to catch.
    return bool(state.get("destroyed"))


def open_run(new_mode: str | None) -> Run:
    """Resume the newest unfinished run, else create one (fire phases only)."""
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    candidates = sorted([d for d in RUNS_ROOT.iterdir() if d.is_dir()], reverse=True)
    for d in candidates:
        r = Run(d)
        if r.state and not r.done("postcheck"):
            if _terminally_finished(r.state):
                # Skipped LOUDLY and counted: a run silently stepped over is how a
                # dam becomes invisible a second time.
                log(f"skipping terminally-finished run {r.state['run_id']} "
                    f"(result={r.state.get('result')!r}, destroyed="
                    f"{bool(r.state.get('destroyed'))}) -- not resumable; its "
                    f"forensics and ledger history are untouched")
                ledger("run_skipped_terminal", r.state["run_id"],
                       result=r.state.get("result"))
                continue
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


# ------------------------------------------------- FU-056: open-run detection
# The 8-day liveness rule asks "was there a recent SUCCESS?". Run 20260719-003024
# opened, paid $1.19, imported half its cohort, died, and never closed -- and that
# question answered "yes" (7/18 had succeeded) while half the moat sat a wave behind
# for two days. An open-but-never-closed run is a different shape, and the ledger has
# carried the evidence the whole time: a `run_opened` with no `run_closed`.
#
# Two shapes look alike in the ledger and must not be conflated, or the detector
# alarms forever and gets ignored -- the decorative-gate failure this project keeps
# rediscovering. Verified against the live ledger (13 runs, 2026-07-14..07-21):
#
#   ABORTED  -- ended out-of-band on purpose: `wedge_guard_destroy`,
#               `manual_destroy_*`, `wedge_check_closed_run`. 10 such runs exist.
#               Informational, never alarming.
#   STRANDED -- the pipeline's own last word was a `phase_*` event and then silence.
#               This is the 7/19 shape. Alarming once older than the threshold.
#
# `phase_destroy_done` and `destroyed` are NOT terminal: they are mid-pipeline (the
# GPU instance is torn down before import/backfill/postcheck), which is exactly why
# run 20260719-003024 looked finished while three phases were still owed.
STALE_OPEN_RUN_HOURS = float(os.environ.get("RESCORE_STALE_OPEN_RUN_HOURS", "24"))
_CLOSED_EVENTS = {"run_closed"}


def _is_abort_event(event: str) -> bool:
    """An out-of-band abandonment, as opposed to a mid-pipeline phase event."""
    if event.startswith("phase_"):
        return False
    return (event.startswith(("wedge_", "manual_"))
            and ("destroy" in event or "closed" in event))


_ABANDON_RESULT_PREFIXES = ("killed", "abort", "abandon", "cancel")


def _state_abandoned(rid: str, runs_root: Path) -> bool:
    """Did the run record its OWN deliberate abandonment, and release its spend?

    FU-132. A run killed BEFORE `fire` never reaches the ledger's abort vocabulary
    (`wedge_*` / `manual_*`) -- the operator's last word lands in the run's own
    state.json as `result: killed_*`. Runs 20260725-170556 and 20260725-181359 were
    both killed at preflight (read-only: no export, no instance, $0) and the detector
    called them STRANDED for 60h, so `--check-open-runs` exited 1 on every invocation
    and would have forever. A gate that is permanently red is a gate nobody reads --
    the decorative-gate failure the header of this section warns about, walked into
    by the very check that warns about it.

    The bar is NOT lowered. The danger `check_open_runs` exists for is a run that
    "opened, SPENT, and never closed", so a state-recorded abandonment only counts
    when the run carries no unreleased spend: it never got an instance, or the
    instance is already destroyed. A fired run still holding an instance stays
    STRANDED no matter what its state.json claims -- and the live-instance API, not
    this function, remains the authoritative guard on that (vast ledger split-brain).

    `result: "ok"` is deliberately NOT an abandonment: a successful run closes via
    the `run_closed` ledger event, and if it did not, that IS the 7/19 shape.
    """
    try:
        st = json.loads((runs_root / rid / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False                   # no state = no evidence = still stranded
    if not isinstance(st, dict):
        return False
    result = str(st.get("result") or "").lower()
    if not result.startswith(_ABANDON_RESULT_PREFIXES):
        return False
    return not st.get("instance_id") or bool(st.get("destroyed"))


def _state_terminal(rid: str, runs_root: Path) -> bool:
    """Terminal by the SAME word `open_run` uses -- FU-321, mirrored (2026-08-31).

    Run 20260822-220319 died at its deadline, was destroyed, and was later
    refused by `open_run` as terminally finished (`run_skipped_terminal`) --
    yet this detector kept calling it STRANDED forever: "deadline" is not an
    abandonment prefix and `run_skipped_terminal` is not an abort event. Two
    instruments disagreeing about one word, again -- this time between
    open_run() and the very check whose header warns about that defect.

    The bar is NOT lowered: _terminally_finished supplies the verdict word,
    and the same spend clause `_state_abandoned` uses is applied on top -- a
    run still holding an instance stays STRANDED whatever its state.json
    claims, and `ok` without `run_closed` remains the stranded shape.
    """
    try:
        st = json.loads((runs_root / rid / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False                   # no state = no evidence = still stranded
    if not isinstance(st, dict):
        return False
    return (_terminally_finished(st)
            and (not st.get("instance_id") or bool(st.get("destroyed"))))


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def open_runs(ledger_path: Path | None = None, now: datetime | None = None,
              include_aborted: bool = False,
              runs_root: Path | None = None) -> list[dict]:
    """Return runs that opened and never reached `run_closed`, newest first.

    Reads the ledger as an event log rather than a success log. Each record carries
    the run id, when it opened, its last observed event, how long it has been open,
    and an `outcome` of "stranded" or "aborted". Aborted runs are excluded by default
    because they were abandoned deliberately and alarming on them is noise.
    """
    path = LEDGER if ledger_path is None else ledger_path
    # The ledger lives at <runs_root>/ledger.jsonl, so the run dirs sit beside it.
    # Deriving the root from the ledger keeps the state.json reconciliation pointed
    # at the SAME tree the caller is auditing (and keeps tests off the live tree).
    root = path.parent if runs_root is None else runs_root
    now = datetime.now(timezone.utc) if now is None else now
    if not path.exists():
        return []
    opened: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue                       # a torn write must not blind the detector
        rid, event = ev.get("run"), ev.get("event")
        if not rid or not event:
            continue
        if event == "run_opened":
            opened.setdefault(rid, {"run_id": rid, "opened_at": ev.get("ts"),
                                    "last_event": event, "last_ts": ev.get("ts")})
        elif rid in opened:
            if event in _CLOSED_EVENTS:
                opened.pop(rid, None)
            else:
                opened[rid]["last_event"] = event
                opened[rid]["last_ts"] = ev.get("ts")
    out = []
    for rec in opened.values():
        aborted = (_is_abort_event(rec["last_event"])
                   or _state_abandoned(rec["run_id"], root)
                   or _state_terminal(rec["run_id"], root))
        rec["outcome"] = "aborted" if aborted else "stranded"
        t0 = _parse_ts(rec.get("opened_at") or "")
        rec["open_hours"] = round((now - t0).total_seconds() / 3600.0, 2) if t0 else None
        # Only a STRANDED run can be stale; an aborted run is already finished.
        rec["stale"] = bool(not aborted and rec["open_hours"] is not None
                            and rec["open_hours"] > STALE_OPEN_RUN_HOURS)
        if aborted and not include_aborted:
            continue
        out.append(rec)
    return sorted(out, key=lambda r: r.get("opened_at") or "", reverse=True)


def check_open_runs(ledger_path: Path | None = None,
                    runs_root: Path | None = None) -> int:
    """CLI check. Exit 1 if any run has been open longer than the stale threshold."""
    runs = open_runs(ledger_path, runs_root=runs_root)
    aborted = [r for r in open_runs(ledger_path, include_aborted=True,
                                    runs_root=runs_root)
               if r["outcome"] == "aborted"]
    stale = [r for r in runs if r["stale"]]
    if aborted:
        log(f"OPEN-RUN CHECK: {len(aborted)} aborted run(s) ignored "
            f"(deliberate out-of-band teardown, not stranded).")
    if not runs:
        log("OPEN-RUN CHECK: no stranded runs.")
        return 0
    for r in runs:
        log(f"STRANDED RUN {r['run_id']}: opened {r['opened_at']} "
            f"({r['open_hours']}h ago), last event {r['last_event']} at {r['last_ts']}"
            f"{'  <-- STALE' if r['stale'] else ''}")
    if stale:
        log(f"OPEN-RUN CHECK FAILED: {len(stale)} run(s) stranded > "
            f"{STALE_OPEN_RUN_HOURS}h. A run that opened, spent, and never closed "
            f"leaves writes half-applied while the liveness rule still reads GREEN "
            f"off the previous success.")
        return 1
    log(f"OPEN-RUN CHECK: {len(runs)} run(s) stranded but none older than "
        f"{STALE_OPEN_RUN_HOURS}h.")
    return 0
