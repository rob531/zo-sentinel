#!/usr/bin/env python3
"""
sentinel_director.py  v2.0.0

T2 mesh agent. Reads SENTINEL_ROADMAP.md and autonomously keeps
zo_sentinel_builder.py furnished with directives.

Behaviours (every POLL_SECS = 300s):
  1. ROADMAP CHECK (every 6h): reads SENTINEL_ROADMAP.md, injects a
     directive for any file that is missing or < MIN_FILE_BYTES.
     Respects phase order: won't inject Phase N+1 until Phase N passes.
  2. QUALITY PASS (every 24h): injects improve directives for files
     that exist but are undersized (< QUALITY_BYTES) -- Ollama stubs.
  3. FAILURE REPAIR (every poll): reads recent build_failed events,
     injects repair directives with failure context embedded.
  4. KB UPDATE (every poll): appends new failure patterns from
     smoke_fail tracebacks to KNOWLEDGE_BASE.md.
  5. STATUS REPORT (every 6h): writes SENTINEL_STATUS.md.

Directives go to mesh_memory agent_id='zo_sentinel.directive' so the
builder picks them up on its next 5-minute poll.
"""
import json, time, logging, requests, hashlib, re
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger(__name__)
WRITE_SERVICE  = "http://127.0.0.1:8772"
PROJECT_DIR    = Path("/home/workspace/zo_sentinel")
ROADMAP_PATH   = PROJECT_DIR / "SENTINEL_ROADMAP.md"
KNOWLEDGE_PATH = PROJECT_DIR / "KNOWLEDGE_BASE.md"
POLL_SECS      = 300    # 5 min
ROADMAP_SECS   = 21600  # 6h
QUALITY_SECS   = 86400  # 24h
MIN_FILE_BYTES = 500    # below this = missing/stub
QUALITY_BYTES  = 2000   # below this = Ollama stub, needs improvement


# ── Roadmap parser ───────────────────────────────────────────────────────────

def parse_roadmap() -> list:
    """
    Parse SENTINEL_ROADMAP.md into a list of module specs.
    Format: filename | complexity | phase | reads (csv) | description
    Returns list of dicts sorted by phase then filename.
    """
    if not ROADMAP_PATH.exists():
        log.warning("Director: SENTINEL_ROADMAP.md not found")
        return []
    modules = []
    for line in ROADMAP_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("##"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            continue
        filename, complexity, phase, reads_raw, description = (
            parts[0], parts[1], parts[2], parts[3], parts[4]
        )
        if not filename.endswith(".py"):
            continue
        reads = [r.strip() for r in reads_raw.split(",") if r.strip()]
        modules.append({
            "filename":    filename,
            "complexity":  complexity,
            "phase":       phase.strip(),
            "reads":       reads,
            "description": description,
        })
    return sorted(modules, key=lambda m: (int(m["phase"]), m["filename"]))


def highest_passing_phase() -> int:
    """
    Determine the highest phase where ALL modules in that phase
    exist and pass the size threshold. Returns that phase number
    so we know Phase N+1 is unlocked.
    """
    modules = parse_roadmap()
    by_phase = {}
    for m in modules:
        ph = int(m["phase"])
        by_phase.setdefault(ph, [])
        by_phase[ph].append(m["filename"])

    highest = 0
    for ph in sorted(by_phase.keys()):
        all_pass = all(
            (PROJECT_DIR / fname).exists() and
            (PROJECT_DIR / fname).stat().st_size >= MIN_FILE_BYTES
            for fname in by_phase[ph]
        )
        if all_pass:
            highest = ph
        else:
            break  # stop at first incomplete phase
    return highest


# ── Write service ─────────────────────────────────────────────────────────────

def ws_query(sql: str) -> list:
    try:
        r = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=8)
        if r.status_code == 200: return r.json().get("rows", [])
    except Exception: pass
    return []

def ws_write(table: str, row: dict) -> bool:
    try:
        r = requests.post(f"{WRITE_SERVICE}/write",
            json={"table": table, "rows": row, "wait": True}, timeout=8)
        return r.status_code == 200
    except Exception: return False

def heartbeat():
    ws_write("service_health", {
        "service": "sentinel_director",
        "last_heartbeat": datetime.now(timezone.utc).isoformat()
    })

def directive_key(task: str, output_file: str) -> str:
    """Deduplicate directives by task+file hash."""
    return hashlib.md5(f"{task}:{output_file}".encode()).hexdigest()[:12]

_injected_keys: set = set()

def inject_directive(task: str, description: str, output_file: str,
                     reads: list, complexity: str, phase: str,
                     priority: float = 0.85, context: str = "",
                     force: bool = False) -> bool:
    """Write a build directive to mesh_memory. Deduplicates by key."""
    key = directive_key(task, output_file)
    if key in _injected_keys and not force:
        return False  # already injected this session
    directive = {
        "task":        task,
        "handler":     "generate_file",
        "description": description,
        "output_file": output_file,
        "reads":       reads,
        "complexity":  complexity,
        "priority":    priority,
        "phase":       phase,
        "context":     context,
        "from":        "sentinel_director",
        "injected_at": datetime.now(timezone.utc).isoformat()
    }
    ok = ws_write("mesh_memory", {
        "agent_id":    "zo_sentinel.directive",
        "memory_type": "build_directive",
        "content":     json.dumps(directive),
        "importance":  priority,
        "created_at":  datetime.now(timezone.utc).isoformat()
    })
    if ok:
        _injected_keys.add(key)
        log.info(f"Director: → [{task}] phase={phase} ({output_file})")
    return ok


# ── Behaviour 1: Roadmap check ─────────────────────────────────────────────────

_last_roadmap_check = datetime.min.replace(tzinfo=timezone.utc)

def check_roadmap():
    """Read roadmap, inject directives for missing/stub modules.
    Unlocks one phase at a time: won't inject Phase N+1 until Phase N complete.
    """
    global _last_roadmap_check
    now = datetime.now(timezone.utc)
    if (now - _last_roadmap_check).total_seconds() < ROADMAP_SECS:
        return
    _last_roadmap_check = now

    modules     = parse_roadmap()
    unlocked_up_to = highest_passing_phase() + 1  # current + next phase
    injected    = 0

    log.info(f"Director: roadmap check — {len(modules)} modules, unlocked up to phase {unlocked_up_to}")

    for m in modules:
        ph = int(m["phase"])
        if ph > unlocked_up_to:
            continue  # not yet unlocked
        fpath = PROJECT_DIR / m["filename"]
        is_missing = not fpath.exists() or fpath.stat().st_size < MIN_FILE_BYTES
        if is_missing:
            task = f"roadmap_p{ph}_{m['filename'].replace('.py','')}"
            inject_directive(
                task=task,
                description=m["description"],
                output_file=m["filename"],
                reads=m["reads"],
                complexity=m["complexity"],
                phase=str(ph),
                priority=0.90 - (ph * 0.01)  # higher phases slightly lower priority
            )
            injected += 1

    if injected:
        log.info(f"Director: {injected} roadmap directive(s) injected")
    else:
        log.info(f"Director: roadmap complete up to phase {unlocked_up_to}")


# ── Behaviour 2: Quality pass ────────────────────────────────────────────────────

_last_quality_check = datetime.min.replace(tzinfo=timezone.utc)

def check_quality():
    """Every 24h: find undersized files (Ollama stubs) and inject improve directives."""
    global _last_quality_check
    now = datetime.now(timezone.utc)
    if (now - _last_quality_check).total_seconds() < QUALITY_SECS:
        return
    _last_quality_check = now

    modules  = parse_roadmap()
    improved = 0
    for m in modules:
        fpath = PROJECT_DIR / m["filename"]
        if not fpath.exists(): continue
        size = fpath.stat().st_size
        # Only improve if file exists but is too small to be real
        if MIN_FILE_BYTES <= size < QUALITY_BYTES:
            task = f"quality_improve_{m['filename'].replace('.py','')}"
            inject_directive(
                task=task,
                description=(
                    f"IMPROVE EXISTING FILE: {m['filename']} is only {size} bytes — "
                    f"likely an Ollama stub. Rewrite as a full production-grade implementation.\n"
                    f"Original spec: {m['description']}"
                ),
                output_file=m["filename"],
                reads=m["reads"],
                complexity="high",  # always high for quality improvements
                phase=m["phase"],
                priority=0.75,  # lower than missing-file priority
                force=True  # override dedup for quality passes
            )
            improved += 1
    if improved:
        log.info(f"Director: {improved} quality improvement directive(s) injected")


# ── Behaviour 3: Failure repair ─────────────────────────────────────────────────

_seen_failure_ids: set = set()

def scan_failures():
    """Inject repair directives for recent build failures."""
    rows = ws_query(
        "SELECT id, payload, created_at FROM mesh_events "
        "WHERE agent_id='t1.zo_sentinel_builder' AND event_type='build_failed' "
        "AND created_at > now() - INTERVAL 2 HOUR "
        "ORDER BY created_at DESC LIMIT 20"
    )
    modules_by_file = {m["filename"]: m for m in parse_roadmap()}

    for row in rows:
        rid = row.get("id")
        if rid in _seen_failure_ids: continue
        _seen_failure_ids.add(rid)
        try:
            payload = json.loads(row.get("payload", "{}"))
            reason  = payload.get("reason", "")
            task    = payload.get("task", "")
            # Find the output file from the task name
            fname   = None
            for f in modules_by_file:
                stem = f.replace(".py","").replace("_","")
                if stem in task.replace("_","").replace("-",""):
                    fname = f
                    break
            if not fname: continue
            fpath = PROJECT_DIR / fname
            if fpath.exists() and fpath.stat().st_size >= QUALITY_BYTES:
                continue  # file is fine now, skip
            m = modules_by_file[fname]
            repair_task = f"repair_{fname.replace('.py','')}"
            inject_directive(
                task=repair_task,
                description=(
                    m["description"] +
                    f"\nPREVIOUS FAILURE ({reason[:150]}): avoid this failure pattern. "
                    "Never use duckdb.connect() directly, never use 'row' instead of 'rows', "
                    "never call write_service() as a function."
                ),
                output_file=fname,
                reads=m["reads"],
                complexity=m["complexity"],
                phase=m["phase"],
                priority=0.95,
                force=True
            )
        except Exception as e:
            log.warning(f"Director: repair scan: {e}")


# ── Behaviour 4: KB update ───────────────────────────────────────────────────────────

_kb_seen: set = set()

def update_knowledge_base():
    """Append new failure patterns from smoke tracebacks to KNOWLEDGE_BASE.md."""
    rows = ws_query(
        "SELECT content FROM mesh_memory "
        "WHERE agent_id='zo_sentinel.smoke_fail' AND memory_type='build_traceback' "
        "AND created_at > now() - INTERVAL 24 HOUR ORDER BY created_at DESC LIMIT 30"
    )
    new_patterns = []
    for row in rows:
        try:
            rec = json.loads(row.get("content", "{}"))
            for fail in rec.get("contract_failures",[]) + rec.get("wiring_warnings",[]):
                h = hashlib.md5(fail.encode()).hexdigest()[:8]
                if h not in _kb_seen:
                    _kb_seen.add(h)
                    new_patterns.append(fail)
        except Exception: pass
    if new_patterns and KNOWLEDGE_PATH.exists():
        existing = KNOWLEDGE_PATH.read_text()
        adds = [p for p in new_patterns if p not in existing]
        if adds:
            with open(KNOWLEDGE_PATH, "a") as f:
                f.write(f"\n## Director-observed ({datetime.now(timezone.utc).strftime('%Y-%m-%d')})\n")
                for a in adds:
                    f.write(f"- AVOID: {a}\n")
            log.info(f"Director: +{len(adds)} pattern(s) to KNOWLEDGE_BASE.md")


# ── Behaviour 5: Status report ─────────────────────────────────────────────────────

_last_report = datetime.min.replace(tzinfo=timezone.utc)

def write_status_report():
    global _last_report
    now = datetime.now(timezone.utc)
    if (now - _last_report).total_seconds() < ROADMAP_SECS:
        return
    _last_report = now

    modules  = parse_roadmap()
    unlocked = highest_passing_phase()
    lines    = [
        f"# ZO-SENTINEL Build Status\n",
        f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')} | "
        f"Unlocked phase: {unlocked}\n\n",
        "## Module Status\n\n"
    ]
    for m in modules:
        fpath = PROJECT_DIR / m["filename"]
        if not fpath.exists():
            icon = "❌"; note = "missing"
        elif fpath.stat().st_size < MIN_FILE_BYTES:
            icon = "⚠️ "; note = f"stub ({fpath.stat().st_size}b)"
        elif fpath.stat().st_size < QUALITY_BYTES:
            icon = "⚡"; note = f"small ({fpath.stat().st_size}b) — may be Ollama stub"
        else:
            icon = "✅"; note = f"{fpath.stat().st_size}b"
        lines.append(f"- {icon} `{m['filename']}` [p{m['phase']}] — {note}\n")

    # Recent events
    events = ws_query(
        "SELECT event_type, payload, created_at FROM mesh_events "
        "WHERE agent_id='t1.zo_sentinel_builder' "
        "AND created_at > now() - INTERVAL 24 HOUR "
        "ORDER BY created_at DESC LIMIT 20"
    )
    lines.append("\n## Recent Build Events (24h)\n\n")
    for ev in events:
        try:
            p   = json.loads(ev.get("payload", "{}"))
            ts  = str(ev.get("created_at", ""))[:16]
            task = p.get("task", Path(p.get("file","")).name)
            lines.append(f"- `{ts}` {ev['event_type']}: {task}\n")
        except Exception: pass

    lines.append("\n## Directive Queue (mesh_memory)\n\n")
    pending = ws_query(
        "SELECT content FROM mesh_memory WHERE agent_id='zo_sentinel.directive' "
        "AND memory_type='build_directive' ORDER BY importance DESC LIMIT 10"
    )
    for row in pending:
        try:
            d = json.loads(row["content"])
            lines.append(f"- `{d.get('task')}` -> {d.get('output_file')} [{d.get('complexity')}]\n")
        except Exception: pass
    if not pending:
        lines.append("- (empty)\n")

    report = "".join(lines)
    (PROJECT_DIR / "SENTINEL_STATUS.md").write_text(report)
    log.info(f"Director: wrote SENTINEL_STATUS.md ({len(report)}b)")


# ── Main loop ─────────────────────────────────────────────────────────────

def run():
    logging.basicConfig(
        level=logging.INFO,
        filename="/home/workspace/logs/sentinel_director.log",
        format="%(asctime)s [director] %(levelname)s: %(message)s"
    )
    modules = parse_roadmap()
    unlocked = highest_passing_phase()
    log.info("=" * 50)
    log.info("Sentinel Director v2.0.0")
    log.info(f"  Roadmap: {len(modules)} modules across phases 2-10")
    log.info(f"  Unlocked: phase {unlocked} complete, phase {unlocked+1} next")
    log.info(f"  Poll: {POLL_SECS}s | Roadmap: {ROADMAP_SECS}s | Quality: {QUALITY_SECS}s")
    log.info("=" * 50)
    # Run roadmap check immediately on startup
    check_roadmap()
    write_status_report()
    while True:
        time.sleep(POLL_SECS)
        try:
            heartbeat()
            scan_failures()
            update_knowledge_base()
            check_roadmap()
            check_quality()
            write_status_report()
        except Exception as e:
            log.error(f"Director cycle: {e}")


if __name__ == "__main__":
    run()