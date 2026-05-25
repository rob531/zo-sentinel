#!/usr/bin/env python3
"""
inject_directive.py  v2.0

CLI tool for injecting build directives into ZO-SENTINEL builder.
Used by Claude (via MCP) and by Robin directly.

v2.0 additions:
  - --retry mode: reads latest traceback from DuckDB for a failed file,
    clips to last 15 lines (4096 token budget), builds a stateful retry
    prompt combining failed code tail + traceback + original description
  - --traceback: manually supply traceback string
  - --failed-file: path to the file that failed (reads last 15 lines)
  - Context clipping: never exceeds ~1200 chars of injected context
    to stay within Ollama's 4096 token limit

Usage — new directive:
  python3 inject_directive.py --task phase4_api \
    --description 'Build FastAPI registry endpoint' \
    --file registry_api.py --complexity high

Usage — retry failed build:
  python3 inject_directive.py --retry --file signal_analyser.py

Usage — retry with manual traceback:
  python3 inject_directive.py --retry --file signal_analyser.py \
    --traceback "AttributeError: module has no attribute 'run'"
"""
import argparse, json, sys, re, requests, hashlib
from datetime import datetime, timezone
from pathlib import Path

WRITE_SERVICE  = "http://127.0.0.1:8772"
DIRECTIVE_DIR  = Path("/home/workspace/zo_sentinel/directives")
PROJECT_DIR    = Path("/home/workspace/zo_sentinel")
DIRECTIVE_DIR.mkdir(parents=True, exist_ok=True)

# Hard clip limits for Ollama 4096-token budget
MAX_CODE_LINES   = 15   # last N lines of failed code
MAX_TB_LINES     = 15   # last N lines of traceback
MAX_CONTEXT_CHARS = 1200  # total injected context chars


# ── Write service helpers ────────────────────────────────────────────────────────

def ws_write(table: str, row: dict) -> bool:
    try:
        r = requests.post(f"{WRITE_SERVICE}/write",
                          json={"table": table, "rows": row, "wait": True},
                          timeout=8)
        return r.status_code == 200
    except Exception:
        return False

def ws_query(sql: str) -> list:
    try:
        r = requests.post(f"{WRITE_SERVICE}/query",
                          json={"sql": sql}, timeout=8)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception:
        pass
    return []


# ── Context clipping ───────────────────────────────────────────────────────────

def clip_lines(text: str, max_lines: int) -> str:
    """Return last max_lines lines of text."""
    if not text:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])

def clip_to_budget(text: str, budget: int) -> str:
    """Hard clip to character budget from the end (most recent = most relevant)."""
    if len(text) <= budget:
        return text
    return "..." + text[-budget:]

def strip_markdown_from_code(code: str) -> str:
    """
    Strip markdown fences from a failed code sample.
    Llama3 sometimes wraps even its first attempt in ```python.
    Must strip before injecting into retry prompt or it confuses the model.
    """
    # Remove ```python...``` blocks
    match = re.search(r'^```python\n(.*?)\n```\s*$', code, re.DOTALL | re.MULTILINE)
    if match:
        return match.group(1)
    # Remove plain ``` blocks
    match = re.search(r'^```\n?(.*?)\n?```\s*$', code, re.DOTALL | re.MULTILINE)
    if match:
        return match.group(1)
    # Strip any fence lines
    lines = [l for l in code.split('\n') if not re.match(r'^```', l.strip())]
    return '\n'.join(lines)


# ── Traceback extraction from DuckDB ────────────────────────────────────────────

def fetch_traceback_from_db(filename: str) -> dict:
    """
    Fetch the most recent build_traceback record for a file from mesh_memory.
    Written by smoke_test.py --write-db on failure.
    Returns dict with keys: failed_code_tail, traceback_tail, wiring_warnings
    """
    rows = ws_query(
        f"SELECT content, created_at FROM mesh_memory "
        f"WHERE agent_id = 'zo_sentinel.smoke_fail' "
        f"AND memory_type = 'build_traceback' "
        f"AND content LIKE '%{filename}%' "
        f"ORDER BY created_at DESC LIMIT 1"
    )
    if not rows:
        return {}
    try:
        return json.loads(rows[0]["content"])
    except Exception:
        return {}


def read_failed_file_tail(filepath: str, max_lines: int = MAX_CODE_LINES) -> str:
    """Read last max_lines of a failed file. Strips markdown before returning."""
    path = Path(filepath)
    if not path.exists():
        path = PROJECT_DIR / filepath
    if not path.exists():
        return ""
    content = path.read_text()
    content = strip_markdown_from_code(content)
    return clip_lines(content, max_lines)


# ── Retry prompt builder ───────────────────────────────────────────────────────────

def build_retry_prompt(filename: str, original_description: str,
                       failed_code_tail: str, traceback_tail: str,
                       wiring_warnings: list) -> str:
    """
    Stateless retry f-string template.
    Combines: original description + clipped failed code + clipped traceback.
    Compensates for the zero-memory bash requeue loop — the model gets full
    context about what went wrong even though it has no memory of prior attempt.

    Total context kept under MAX_CONTEXT_CHARS to fit Ollama's 4096 token budget.
    """
    # Clip each section to its line budget
    code_section = clip_lines(failed_code_tail, MAX_CODE_LINES)
    tb_section   = clip_lines(traceback_tail,   MAX_TB_LINES)

    # Build wiring warning line if present
    wire_line = ""
    if wiring_warnings:
        wire_line = f"\nFix these wiring errors too: {'; '.join(wiring_warnings[:3])}"

    # Compose retry prompt
    retry_context = (
        f"RETRY ATTEMPT. Your previous code for {filename} failed.\n"
        f"\n"
        f"=== LAST 15 LINES OF YOUR PREVIOUS CODE ===\n"
        f"{code_section}\n"
        f"\n"
        f"=== TRACEBACK FROM THAT CODE ===\n"
        f"{tb_section}\n"
        f"{wire_line}\n"
        f"\n"
        f"Fix the above errors. Write the complete corrected file.\n"
    )

    # Hard clip total context to stay in budget
    retry_context = clip_to_budget(retry_context, MAX_CONTEXT_CHARS)

    # Combine with original task description
    return f"{retry_context}\nORIGINAL TASK: {original_description[:400]}"


# ── Directive injection ───────────────────────────────────────────────────────────

def inject(directive: dict):
    task = directive.get("task", "unknown")
    now  = datetime.now(timezone.utc).isoformat()
    directive["injected_at"] = now
    directive.setdefault("handler",    "generate_file")
    directive.setdefault("complexity", "medium")
    directive.setdefault("priority",   0.8)

    # Write to file (always — works even if write_service down)
    idx   = len(list(DIRECTIVE_DIR.glob("*.json"))) + 1
    fpath = DIRECTIVE_DIR / f"{idx:03d}_{task}.json"
    fpath.write_text(json.dumps(directive, indent=2))
    print(f"[+] Directive written: {fpath}")

    # Write to mesh_memory (best effort)
    ok = ws_write("mesh_memory", {
        "agent_id":    "zo_sentinel.directive",
        "memory_type": "build_directive",
        "content":     json.dumps(directive),
        "importance":  directive.get("priority", 0.8),
        "created_at":  now
    })
    status = "mesh + file" if ok else "file only (mesh down)"
    print(f"[+] {task} → {status}")
    print(f"[*] Builder processes on next poll (max 5 min)")


# ── CLI ─────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Inject ZO-SENTINEL build directive")

    # Mode flags
    p.add_argument("--retry",       action="store_true",
                   help="Retry mode: read traceback from DB and build stateful retry prompt")
    p.add_argument("--json",        help="Path to JSON directive file")

    # Core directive fields
    p.add_argument("--task",        help="Task identifier")
    p.add_argument("--description", help="What to build")
    p.add_argument("--file",        dest="output_file", help="Output filename")
    p.add_argument("--complexity",  default="medium", choices=["low","medium","high"])
    p.add_argument("--priority",    type=float, default=0.8)
    p.add_argument("--context",     default="", help="Additional context")
    p.add_argument("--handler",     default="generate_file")
    p.add_argument("--reads",       nargs="*", default=[],
                   help="Dependency files to inject into prompt")

    # Retry-specific flags
    p.add_argument("--traceback",   default="",
                   help="Traceback string (retry mode: overrides DB lookup)")
    p.add_argument("--failed-file", dest="failed_file", default="",
                   help="Path to failed file (retry mode: reads last 15 lines)")

    args = p.parse_args()

    # ─ JSON directive mode ─
    if args.json:
        with open(args.json) as f:
            directive = json.load(f)
        inject(directive)
        return

    # ─ Require task + file for all other modes ─
    if not args.output_file:
        p.print_help(); sys.exit(1)

    output_file  = args.output_file
    task         = args.task or f"retry_{output_file.replace('.py','')}"
    description  = args.description or f"Rebuild {output_file}"
    failed_file  = args.failed_file or output_file

    # ─ Retry mode: build stateful prompt ─
    if args.retry:
        print(f"[retry] Building stateful retry prompt for {output_file}")

        # Fetch traceback from DB (written by smoke_test.py --write-db)
        db_record = fetch_traceback_from_db(output_file)

        # Resolve code tail: DB record > file read > empty
        if db_record.get("failed_code_tail"):
            code_tail = db_record["failed_code_tail"]
            print(f"[retry]   code tail from DB ({len(code_tail)} chars)")
        elif args.failed_file or (PROJECT_DIR / output_file).exists():
            code_tail = read_failed_file_tail(failed_file)
            print(f"[retry]   code tail from file ({len(code_tail)} chars)")
        else:
            code_tail = ""
            print(f"[retry]   no code tail available")

        # Resolve traceback: CLI flag > DB record > empty
        if args.traceback:
            tb_tail = clip_lines(args.traceback, MAX_TB_LINES)
            print(f"[retry]   traceback from CLI")
        elif db_record.get("traceback_tail"):
            tb_tail = db_record["traceback_tail"]
            print(f"[retry]   traceback from DB")
        else:
            tb_tail = ""
            print(f"[retry]   no traceback available — using empty")

        wiring_warns = db_record.get("wiring_warnings", [])

        # Build stateless retry prompt
        retry_description = build_retry_prompt(
            filename=output_file,
            original_description=description,
            failed_code_tail=code_tail,
            traceback_tail=tb_tail,
            wiring_warnings=wiring_warns
        )

        print(f"[retry]   prompt length: {len(retry_description)} chars "
              f"(budget: {MAX_CONTEXT_CHARS})")

        directive = {
            "task":        task,
            "handler":     args.handler,
            "description": retry_description,
            "output_file": output_file,
            "complexity":  args.complexity,
            "priority":    min(0.95, args.priority + 0.1),  # bump priority for retries
            "context":     args.context,
            "reads":       args.reads,
            "from":        "cli_retry",
            "is_retry":    True
        }

    # ─ Normal new directive mode ─
    else:
        if not args.task or not args.description:
            print("ERROR: --task and --description required for new directives")
            p.print_help(); sys.exit(1)

        directive = {
            "task":        args.task,
            "handler":     args.handler,
            "description": args.description,
            "output_file": output_file,
            "complexity":  args.complexity,
            "priority":    args.priority,
            "context":     args.context,
            "reads":       args.reads,
            "from":        "cli_inject"
        }

    inject(directive)


if __name__ == "__main__":
    main()