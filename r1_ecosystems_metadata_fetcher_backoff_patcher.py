#!/usr/bin/env python3
"""
r1_ecosystems_metadata_fetcher_backoff_patcher.py
Adds exponential backoff, Retry-After support, and token-bucket rate limiting
to ecosystems_metadata_fetcher.py.
Exit codes: 0=applied/noop, 1=shape unrecognized, 2=syntax failed, 3=smoke failed
"""
import argparse
import ast
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

WRITE_SERVICE = "http://127.0.0.1:8772"
WRITE_URL = f"{WRITE_SERVICE}/write"
LOGS_DIR = Path("/home/workspace/logs")
LOG_FILE = LOGS_DIR / "ecosystems_429.log"
SOURCE_FILE = Path("/home/workspace/zo_sentinel/ecosystems_metadata_fetcher.py")
BACKUP_EXT_PREFIX = ".bak"
MARKER = "# _zo_backoff_v1"

RATE_BUDGETS = {
    "npm": {"calls": 10, "window": 60, "key": "rate_budget_npm"},
    "pypi": {"calls": 5, "window": 60, "key": "rate_budget_pypi"},
    "github": {"calls": 30, "window": 3600, "key": "rate_budget_github"},
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def log(msg):
    print(f"[{utc_now()}] {msg}", flush=True)


def log_429(source, endpoint, status_code, retry_after, tokens_remaining):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(f"{utc_now()}|source={source}|endpoint={endpoint}|status={status_code}|retry_after={retry_after}|tokens={tokens_remaining}\n")


def get_rate_budget(source: str) -> dict:
    import requests as _req
    try:
        resp = _req.post(WRITE_URL, json={
            "table": "service_health",
            "rows": {"service": RATE_BUDGETS[source]["key"], "last_heartbeat": utc_now()},
            "wait": True
        }, timeout=5)
        resp.raise_for_status()
        query_resp = _req.post(f"{WRITE_SERVICE}/query", json={
            "sql": f"SELECT last_heartbeat, meta FROM service_health WHERE service = '{RATE_BUDGETS[source]['key']}'"
        }, timeout=5)
        if query_resp.ok:
            data = query_resp.json()
            if data.get("rows"):
                row = data["rows"][0]
                hb = row.get("last_heartbeat", "")
                meta = row.get("meta", {}) or {}
                return {"tokens_remaining": meta.get("tokens_remaining", RATE_BUDGETS[source]["calls"]), "refill_at": meta.get("refill_at", ""), "last_heartbeat": hb}
    except Exception:
        pass
    return {"tokens_remaining": RATE_BUDGETS[source]["calls"], "refill_at": utc_now(), "last_heartbeat": utc_now()}


def save_rate_budget(source: str, tokens_remaining: int, refill_at: str):
    import requests as _req
    try:
        _req.post(WRITE_URL, json={
            "table": "service_health",
            "rows": {
                "service": RATE_BUDGETS[source]["key"],
                "last_heartbeat": utc_now(),
                "meta": {"tokens_remaining": tokens_remaining, "refill_at": refill_at}
            },
            "wait": True
        }, timeout=5)
    except Exception as e:
        log(f"WARNING: failed to persist rate budget for {source}: {e}")


def check_token_bucket(source: str) -> bool:
    budget = get_rate_budget(source)
    now_ts = datetime.now(timezone.utc)
    refill_at_str = budget.get("refill_at", utc_now())
    try:
        refill_at = datetime.fromisoformat(refill_at_str)
    except Exception:
        refill_at = now_ts
    if now_ts >= refill_at:
        tokens = RATE_BUDGETS[source]["calls"]
        refill_delta = RATE_BUDGETS[source]["window"]
        from datetime import timedelta
        refill_at_new = (now_ts + timedelta(seconds=refill_delta)).isoformat()
        save_rate_budget(source, tokens, refill_at_new)
        return True
    tokens = budget.get("tokens_remaining", RATE_BUDGETS[source]["calls"])
    if tokens > 0:
        save_rate_budget(source, tokens - 1, refill_at_str)
        return True
    log(f"RATE_LIMIT: {source} bucket empty, waiting for refill at {refill_at_str}")
    return False


def _zo_backoff_sleep(resp_or_exc, retries: int, source: str) -> float:
    sleep_sec = min(2 ** retries, 32.0)
    retry_after = None
    if resp_or_exc is not None:
        if hasattr(resp_or_exc, "headers"):
            ra = resp_or_exc.headers.get("Retry-After", "")
            if ra:
                try:
                    if ra.isdigit():
                        retry_after = int(ra)
                    else:
                        dt = parsedate_to_datetime(ra)
                        retry_after = max(0, (dt - datetime.now(timezone.utc)).total_seconds())
                except Exception:
                    retry_after = None
        status = getattr(resp_or_exc, "status_code", None)
        if status in (429, 503):
            log_429(source, getattr(resp_or_exc, "url", "?"), status, retry_after, get_rate_budget(source).get("tokens_remaining", 0))
    if retry_after is not None and retry_after > 0:
        sleep_sec = min(retry_after, 300.0)
    log(f"backoff: source={source} retries={retries} sleep={sleep_sec:.1f}s")
    time.sleep(sleep_sec)
    return sleep_sec


WRAPPED_SOURCE = '''
# _zo_backoff_v1
import time as _time
from datetime import datetime, timedelta, timezone as _tz
from email.utils import parsedate_to_datetime as _parse_dt

_RATE_BUDGETS = {
    "npm":    {"calls": 10, "window": 60,  "key": "rate_budget_npm"},
    "pypi":   {"calls": 5,  "window": 60,  "key": "rate_budget_pypi"},
    "github": {"calls": 30, "window": 3600, "key": "rate_budget_github"},
}

_WRITE_SVC = "http://127.0.0.1:8772"

def _zo_log_429(source, endpoint, status_code, retry_after, tokens_remaining):
    try:
        from pathlib import Path
        LOGS = Path("/home/workspace/logs")
        LOGS.mkdir(parents=True, exist_ok=True)
        with open(LOGS / "ecosystems_429.log", "a") as f:
            from datetime import datetime as _dt
            ts = _dt.now(_tz.utc).isoformat()
            f.write(f"{ts}|source={source}|endpoint={endpoint}|status={status_code}|retry_after={retry_after}|tokens={tokens_remaining}\\n")
    except Exception:
        pass

def _zo_get_budget(source: str) -> dict:
    import requests as _req
    try:
        _req.post(_WRITE_SVC + "/write", json={
            "table": "service_health",
            "rows": {"service": _RATE_BUDGETS[source]["key"], "last_heartbeat": _dt.now(_tz.utc).isoformat()},
            "wait": True
        }, timeout=5)
        q = _req.post(_WRITE_SVC + "/query", json={
            "sql": f"SELECT last_heartbeat, meta FROM service_health WHERE service = '{_RATE_BUDGETS[source]['key']}'"
        }, timeout=5)
        if q.ok and q.json().get("rows"):
            r = q.json()["rows"][0]
            return {"tokens": r.get("meta", {}).get("tokens_remaining", _RATE_BUDGETS[source]["calls"]),
                    "refill": r.get("meta", {}).get("refill_at", _dt.now(_tz.utc).isoformat())}
    except Exception:
        pass
    return {"tokens": _RATE_BUDGETS[source]["calls"], "refill": _dt.now(_tz.utc).isoformat()}

def _zo_save_budget(source: str, tokens: int, refill: str):
    import requests as _req
    try:
        _req.post(_WRITE_SVC + "/write", json={
            "table": "service_health",
            "rows": {"service": _RATE_BUDGETS[source]["key"], "last_heartbeat": _dt.now(_tz.utc).isoformat(),
                     "meta": {"tokens_remaining": tokens, "refill_at": refill}},
            "wait": True
        }, timeout=5)
    except Exception:
        pass

def _zo_token_ok(source: str) -> bool:
    b = _zo_get_budget(source)
    now = _dt.now(_tz.utc)
    try:
        refill_dt = datetime.fromisoformat(b["refill"])
    except Exception:
        refill_dt = now
    if now >= refill_dt:
        refill_new = (now + timedelta(seconds=_RATE_BUDGETS[source]["window"])).isoformat()
        _zo_save_budget(source, _RATE_BUDGETS[source]["calls"] - 1, refill_new)
        return True
    if b["tokens"] > 0:
        _zo_save_budget(source, b["tokens"] - 1, b["refill"])
        return True
    return False

def _zo_wait_token(source: str):
    b = _zo_get_budget(source)
    try:
        refill_dt = datetime.fromisoformat(b["refill"])
    except Exception:
        refill_dt = _dt.now(_tz.utc)
    wait_s = max(0, (refill_dt - _dt.now(_tz.utc)).total_seconds())
    if wait_s > 0:
        import time as _t
        _t.sleep(min(wait_s, 300))
    b2 = _zo_get_budget(source)
    _zo_save_budget(source, b2["tokens"] - 1, b2["refill"])

def _zo_backoff_sleep(resp_or_exc, retries: int, source: str) -> float:
    sleep_sec = min(2 ** retries, 32.0)
    retry_after = None
    if resp_or_exc is not None and hasattr(resp_or_exc, "headers"):
        ra = resp_or_exc.headers.get("Retry-After", "")
        if ra:
            try:
                if ra.isdigit():
                    retry_after = int(ra)
                else:
                    retry_after = max(0, (_parse_dt(ra) - _dt.now(_tz.utc)).total_seconds())
            except Exception:
                retry_after = None
        status = getattr(resp_or_exc, "status_code", None)
        if status in (429, 503):
            bk = _zo_get_budget(source)
            _zo_log_429(source, getattr(resp_or_exc, "url", "?"), status, retry_after, bk.get("tokens", 0))
    if retry_after is not None and retry_after > 0:
        sleep_sec = min(retry_after, 300.0)
    _time.sleep(sleep_sec)
    return sleep_sec

def _zo_request_with_backoff(url, headers=None, timeout=None, source="npm", max_retries=5):
    import requests as _req
    for attempt in range(max_retries + 1):
        while not _zo_token_ok(source):
            _zo_wait_token(source)
        try:
            resp = _req.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                _zo_backoff_sleep(resp, attempt, source)
                continue
            if resp.status_code == 503:
                _zo_backoff_sleep(resp, attempt, source)
                continue
            return resp
        except _req.exceptions.Timeout:
            _zo_backoff_sleep(None, attempt, source)
        except _req.exceptions.ConnectionError:
            _zo_backoff_sleep(None, attempt, source)
    return _req.get(url, headers=headers, timeout=timeout)

'''


def add_source_to_module_body(body_lines, insert_after_idx):
    indent = "    "
    lines = WRAPPED_SOURCE.split("\n")
    result = []
    for i, line in enumerate(body_lines):
        result.append(line)
        if i == insert_after_idx:
            for src_line in lines:
                result.append(src_line)
    return result


def patch_source(content: str) -> str:
    if MARKER in content:
        return None
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    try:
        import_line_idx = None
        for i, node in enumerate(tree.body):
            if isinstance(node, ast.Import):
                import_line_idx = node.lineno - 1
                break
        if import_line_idx is None:
            for i, node in enumerate(tree.body):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    import_line_idx = node.lineno - 2
                    break
    except Exception:
        import_line_idx = None

    lines = content.split("\n")
    if import_line_idx is None or import_line_idx < 0:
        import_line_idx = 0

    patched = add_source_to_module_body(lines, import_line_idx)
    patched.append(f"\n{MARKER}\n")

    patched_lines = []
    in_fetch_func = False
    fetch_func_indent = None
    wrapped_call_pattern = re.compile(r"^\s*resp\s*=\s*requests\.get\(")
    post_call_pattern = re.compile(r"^\s*resp\s*=\s*requests\.post\(")
    direct_get_pattern = re.compile(r"^\s*resp\s*=\s*_req\.get\(")
    direct_post_pattern = re.compile(r"^\s*resp\s*=\s*_req\.post\(")
    requests_import_pattern = re.compile(r"^\s*resp\s*=\s*requests\.get\(")
    skip_indent = None

    for line in patched:
        stripped = line.lstrip()
        indent_str = line[:len(line) - len(stripped)] if stripped else line

        if stripped.startswith("def fetch_metadata") or stripped.startswith("async def fetch_metadata"):
            in_fetch_func = True
            patched_lines.append(line)
            continue

        if in_fetch_func:
            if stripped and not stripped.startswith("#") and not stripped.startswith('"""') and not stripped.startswith("'''"):
                curr_indent = len(indent_str)
                if fetch_func_indent is None:
                    fetch_func_indent = curr_indent
                    skip_indent = curr_indent

                if curr_indent <= fetch_func_indent and stripped and not stripped.startswith("def ") and not stripped.startswith("class "):
                    in_fetch_func = False
                    patched_lines.append(line)
                    continue

            if requests_import_pattern.match(stripped) or post_call_pattern.match(stripped):
                url_match = re.search(r"requests\.get\((.*?)\)", stripped) or re.search(r"requests\.post\((.*?)\)", stripped)
                if url_match:
                    url_arg = url_match.group(1).strip()
                    if "source=" not in url_arg and "source =" not in url_arg:
                        source_hint = "npm"
                        if "pypi" in url_arg.lower():
                            source_hint = "pypi"
                        elif "github" in url_arg.lower():
                            source_hint = "github"
                        line = re.sub(
                            r"resp\s*=\s*requests\.get\(",
                            f"resp = _zo_request_with_backoff(",
                            line
                        )
                        line = re.sub(
                            r"resp\s*=\s*requests\.post\(",
                            f"resp = _zo_request_with_backoff(",
                            line
                        )
                        line = re.sub(r"\)$", f', source="{source_hint}")', line)
        patched_lines.append(line)

    return "\n".join(patched_lines)


def check_syntax(path: Path) -> bool:
    try:
        ast.parse(path.read_text())
        return True
    except SyntaxError as e:
        log(f"SYNTAX ERROR: {e}")
        return False


def smoke_test() -> bool:
    try:
        sys.path.insert(0, "/home/workspace/zo_sentinel")
        import importlib
        mod = importlib.import_module("ecosystems_metadata_fetcher")
        fn_names = ["fetch_metadata", "run", "cycle", "main"]
        found = [n for n in fn_names if hasattr(mod, n)]
        if not found:
            log(f"SMOKE FAIL: none of {fn_names} found, got: {dir(mod)}")
            return False
        log(f"SMOKE OK: functions found: {found}")
        return True
    except Exception as e:
        log(f"SMOKE FAIL: {e}")
        return False


def make_backup(source: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bak = source.with_name(source.name + f".bak.{ts}")
    shutil.copy2(source, bak)
    log(f"BACKUP: {source} -> {bak}")
    return bak


def main():
    parser = argparse.ArgumentParser(description="Patch ecosystems_metadata_fetcher.py with backoff + rate limiting")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    args = parser.parse_args()

    if not SOURCE_FILE.exists():
        log(f"ERROR: source file not found: {SOURCE_FILE}")
        sys.exit(1)

    content = SOURCE_FILE.read_text()

    if MARKER in content:
        log("NOOP: already hardened")
        sys.exit(0)

    patched = patch_source(content)
    if patched is None:
        log("SHAPE UNRECOGNIZED: could not parse source or no import block found")
        sys.exit(1)

    if args.dry_run:
        import difflib
        diff = difflib.unified_diff(content.splitlines(), patched.splitlines(), lineterm="", n=3)
        for line in list(diff)[:80]:
            print(line)
        log("DRY-RUN: would apply patch, not writing")
        sys.exit(0)

    bak = make_backup(SOURCE_FILE)
    SOURCE_FILE.write_text(patched)
    log("PATCHED: wrote updated ecosystems_metadata_fetcher.py")

    if not check_syntax(SOURCE_FILE):
        log("SYNTAX CHECK FAILED: rolling back")
        shutil.copy2(bak, SOURCE_FILE)
        sys.exit(2)

    if not smoke_test():
        log("SMOKE TEST FAILED: rolling back")
        shutil.copy2(bak, SOURCE_FILE)
        sys.exit(3)

    log("SUCCESS: patch applied, syntax OK, smoke OK")
    sys.exit(0)


if __name__ == "__main__":
    main()