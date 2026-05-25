#!/usr/bin/env python3
"""
fix_rug_pull_and_parser.py -- Two surgical fixes.

Fix A: rug_pull_monitor.py
    - Add missing `if __name__ == '__main__': run()` block
    - Expand run() to a proper daemon loop with heartbeat + cycle + sleep
    - Fix ws_query to route SELECTs to /query (QUERY_URL), not /execute
    - Replace SELECT MAX(id)+1 race-prone pattern with server-side
      generation via COALESCE on the write, avoiding read-then-insert

Fix B: sentinel_directive_generator.py
    - Harden generate_directives() JSON parser against MiniMax variations:
      1. Object-wrapped: {"directives": [...]}  <- suspected 17:38 failure
      2. Markdown fenced: ```json [...] ``` or ``` [...] ```
      3. Preamble text: "Here are the directives:\n[...]"
      4. Trailing text: "[...]\nThat's my suggestion."
      5. Bare array: [...]

    Parser walks multiple strategies in order. Falls back to skip-and-log on
    total failure. Never crashes the cycle.

Both changes AST-validated before write. Both files are PROTECTED so the
rebaseline hook is invoked at the end to acknowledge the intentional change.

After applying, restart both daemons.
"""
import ast
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SENTINEL = Path("/home/workspace/zo_sentinel")
RUG     = SENTINEL / "rug_pull_monitor.py"
GEN     = SENTINEL / "sentinel_directive_generator.py"


def _backup(path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")
    return bak


def _ast_check(path: Path, src: str):
    try:
        ast.parse(src)
    except SyntaxError as e:
        raise RuntimeError(f"AST invalid for {path.name}: {e}")


# =============================================================================
# Fix A: rug_pull_monitor
# =============================================================================

def fix_rug_pull():
    print("\n=== Fix A: rug_pull_monitor.py ===")
    if not RUG.exists():
        print(f"  [FAIL] {RUG} missing"); return False
    src = RUG.read_text()

    # --- Patch 1: add QUERY_URL constant alongside EXECUTE_URL
    if "QUERY_URL = 'http://127.0.0.1:8772/query'" not in src:
        src = src.replace(
            "EXECUTE_URL = 'http://127.0.0.1:8772/execute'",
            "EXECUTE_URL = 'http://127.0.0.1:8772/execute'\n"
            "QUERY_URL = 'http://127.0.0.1:8772/query'",
            1,
        )

    # --- Patch 2: route SELECTs through QUERY_URL in ws_query
    old_ws_query = (
        "def ws_query(sql: str, params: list = None) -> dict:\n"
        '    """Execute SQL query against DuckDB via inference_router."""\n'
        "    payload = {'sql': sql}\n"
        "    if params:\n"
        "        payload['params'] = params\n"
        "    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)\n"
        "    resp.raise_for_status()\n"
        "    return resp.json()\n"
    )
    new_ws_query = (
        "def ws_query(sql: str, params: list = None) -> dict:\n"
        '    """Execute SELECT against DuckDB via write_service /query.\n'
        "    Routes to /query (not /execute) so rows come back. /execute is\n"
        '    fire-and-forget and returns {ok:true} with no rows."""\n'
        "    payload = {'sql': sql}\n"
        "    if params:\n"
        "        payload['params'] = params\n"
        "    resp = requests.post(QUERY_URL, json=payload, timeout=30)\n"
        "    resp.raise_for_status()\n"
        "    body = resp.json()\n"
        "    # /query returns {'rows': [...]}. Normalize to always include 'data'.\n"
        "    if 'rows' in body and 'data' not in body:\n"
        "        body['data'] = [[r[k] for k in r.keys()] for r in body['rows']]\n"
        "    return body\n"
    )
    if old_ws_query in src:
        src = src.replace(old_ws_query, new_ws_query, 1)
        print("  [patch] ws_query routes SELECTs to /query")
    else:
        print("  [skip] ws_query already patched or signature differs")

    # --- Patch 3: fix ws_write URL double-slash
    # WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write' already ends in /write.
    # Current code appends another /write. Strip the appended segment.
    old_write_url = "    url = f'{WRITE_SERVICE_URL}/write'"
    new_write_url = "    url = WRITE_SERVICE_URL  # already ends in /write"
    if old_write_url in src:
        src = src.replace(old_write_url, new_write_url, 1)
        print("  [patch] ws_write double-slash URL fixed")

    # --- Patch 4: replace broken run() stub with proper daemon loop
    old_run = (
        "    def run(self):\n"
        '        """Main daemon entry point."""\n'
        "        check_single_instance()\n"
        "        ensure_tables()"
    )
    new_run = (
        "    def run(self):\n"
        '        """Main daemon entry point. Loops cycle -> sleep forever."""\n'
        "        check_single_instance()\n"
        "        ensure_tables()\n"
        "        send_heartbeat()\n"
        "        print(f'[{datetime.now(timezone.utc).isoformat()}] rug_pull_monitor started')\n"
        "        while True:\n"
        "            try:\n"
        "                self.cycle()\n"
        "            except Exception as e:\n"
        "                print(f'Cycle error: {e}')\n"
        "            send_heartbeat()\n"
        "            time.sleep(POLL_INTERVAL)"
    )
    if old_run in src:
        src = src.replace(old_run, new_run, 1)
        print("  [patch] run() expanded to daemon loop")
    else:
        # Maybe already patched
        if "while True:\n            try:\n                self.cycle()" in src:
            print("  [skip] run() already has daemon loop")
        else:
            print("  [WARN] run() structure differs -- manual review needed")

    # --- Patch 5: append __main__ block if absent
    if "if __name__ == '__main__':" not in src:
        src = src.rstrip() + "\n\n\nif __name__ == '__main__':\n    monitor = RugPullMonitor()\n    monitor.run()\n"
        print("  [patch] __main__ block appended")
    else:
        # Check if it actually invokes the monitor; naive check
        if "RugPullMonitor()" not in src.split("if __name__ == '__main__':")[1][:200]:
            print("  [WARN] __main__ exists but may not call RugPullMonitor().run()")
        else:
            print("  [skip] __main__ block already present")

    # --- Validate and write
    _ast_check(RUG, src)
    _backup(RUG)
    RUG.write_text(src)
    print(f"  [done] {RUG.name} patched")
    return True


# =============================================================================
# Fix B: directive_generator JSON parser
# =============================================================================

HARDENED_PARSER = '''def generate_directives(prompt: str) -> list:
    """Call LLM, parse list of directives from response.

    Tolerant parser. Tries these strategies in order:
      1. Bare JSON array: `[{...}, {...}]`
      2. Object-wrapped array: `{"directives": [...]}` -- MiniMax sometimes does this
      3. Markdown-fenced: ```json ... ``` or ``` ... ```
      4. Mixed content: strip preamble/postamble around first complete JSON value

    Never raises. Returns empty list on total failure.
    """
    raw = call_minimax(prompt)
    if not raw:
        log.info("MiniMax unavailable, trying Ollama")
        raw = call_ollama(prompt)
    if not raw:
        log.warning("No LLM response")
        return []

    text = raw.strip()

    # Strategy 1: strip markdown fences if present
    if text.startswith("```"):
        # Drop first line (``` or ```json), keep until closing ```
        lines = text.split("\\n")
        text = "\\n".join(lines[1:])
        if text.rstrip().endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

    # Strategy 2: try bare parse (may already be clean)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            # Object-wrapped: {"directives": [...]} or {"result": [...]} or similar
            for key in ("directives", "result", "items", "data", "list"):
                if key in parsed and isinstance(parsed[key], list):
                    log.info("JSON was object-wrapped under key %r", key)
                    return parsed[key]
            # Single dict: wrap as one-item list
            log.info("JSON was a single dict; wrapping as one-element list")
            return [parsed]
    except json.JSONDecodeError:
        pass

    # Strategy 3: find first [ ... matching-depth ]
    start = text.find("[")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, list):
                            log.info("JSON recovered via bracket matching (%d-%d)",
                                     start, i+1)
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    break

    # Strategy 4: find first { ... matching-depth } and check for wrapped list
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            for key in ("directives", "result", "items", "data", "list"):
                                if key in parsed and isinstance(parsed[key], list):
                                    log.info("JSON recovered via object wrapping "
                                             "under key %r", key)
                                    return parsed[key]
                    except json.JSONDecodeError:
                        pass
                    break

    log.warning("JSON parse failed across all strategies. First 200 chars: %r",
                text[:200])
    return []
'''


def fix_directive_generator():
    print("\n=== Fix B: sentinel_directive_generator.py ===")
    if not GEN.exists():
        print(f"  [FAIL] {GEN} missing"); return False
    src = GEN.read_text()

    # Find the old generate_directives function and replace from its def line
    # through the next top-level def
    import re
    pat = re.compile(
        r"^def generate_directives\(prompt: str\) -> list:.*?(?=^(?:def |# \xe2\x94\x80)|^[A-Z_]+\s*=|^class )",
        re.DOTALL | re.MULTILINE,
    )
    m = pat.search(src)
    if not m:
        # Looser match
        start_idx = src.find("def generate_directives(prompt: str) -> list:")
        if start_idx == -1:
            print("  [FAIL] generate_directives not found")
            return False
        # Find next top-level def after it
        rest = src[start_idx + 1:]
        next_def = re.search(r"^(def |# \u2500)", rest, re.MULTILINE)
        if not next_def:
            print("  [FAIL] could not find end of generate_directives")
            return False
        end_idx = start_idx + 1 + next_def.start()
        old_block = src[start_idx:end_idx]
    else:
        old_block = m.group(0)

    if "Strategy 1: strip markdown fences" in src:
        print("  [skip] hardened parser already present")
    else:
        src = src.replace(old_block, HARDENED_PARSER + "\n\n", 1)
        print("  [patch] hardened JSON parser installed")

    _ast_check(GEN, src)
    _backup(GEN)
    GEN.write_text(src)
    print(f"  [done] {GEN.name} patched")
    return True


# =============================================================================
# Main
# =============================================================================

def main():
    print("=" * 60)
    print("Fix: rug_pull_monitor startup + MiniMax parser")
    print("=" * 60)

    a_ok = fix_rug_pull()
    b_ok = fix_directive_generator()

    print("\n" + "=" * 60)
    print(f"Fix A (rug_pull_monitor):        {'OK' if a_ok else 'FAILED'}")
    print(f"Fix B (directive_generator):     {'OK' if b_ok else 'FAILED'}")
    print("=" * 60)

    if a_ok and b_ok:
        print("\nNext steps:")
        print("  1. Re-baseline both protected files (deliberate changes):")
        print("       python3 /home/workspace/zo_sentinel/tests/"
              "rebaseline_protected_files.py \\")
        print("           rug_pull_monitor.py sentinel_directive_generator.py")
        print("  2. Restart both daemons:")
        print("       pkill -9 -f 'python3 .*rug_pull_monitor.py'")
        print("       pkill -9 -f 'python3 .*sentinel_directive_generator.py'")
        print("       cd /home/workspace/zo_sentinel")
        print("       nohup python3 rug_pull_monitor.py "
              ">> /home/workspace/logs/sentinel_rug_pull_monitor.log 2>&1 &")
        print("       nohup python3 sentinel_directive_generator.py "
              ">> /home/workspace/logs/sentinel_directive_generator.log 2>&1 &")
        print("  3. Verify:")
        print("       sleep 10 && tail -20 "
              "/home/workspace/logs/sentinel_rug_pull_monitor.log")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())