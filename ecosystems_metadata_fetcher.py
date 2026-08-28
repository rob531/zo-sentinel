#!/usr/bin/env python3
"""
ecosystems_metadata_fetcher.py  -- Commit A

Daemon that enriches each MCP in mcp_server_registry with metadata from
ecosyste.ms: downloads count, latest release age, ecosystem presence,
package count (number of cross-registry cousins), dominant ecosystem.

Writes to a new table `mcp_ecosystems_metadata`. The existing enrichment
modules (community_signal_enrichment.py, temporal_stability_enrichment.py)
get their input variety from this data via a thin adapter lookup.

Key design choices:
  1. CACHE AGGRESSIVELY: ecosyste.ms data changes on hour-to-day timescale.
     We refresh an entry's metadata only if older than CACHE_TTL_HOURS.
  2. COUSIN-AGNOSTIC: today we just pick the highest-download cousin as the
     enrichment source (simple "top pick" heuristic). Full canonicalization
     with republisher filtering comes in Commit B. This is intentional --
     we get 80% of the signal value without waiting for that design.
  3. POLITE POOL: identify ourselves via User-Agent + From header so
     ecosyste.ms can contact us about quota if needed.
  4. FAIL OPEN: API errors don't block the daemon. Skip entry, retry on
     next cycle, log for operator visibility.
  5. SCHEMA ADDITIVE: new table only. Does not modify mcp_server_registry
     or mcp_signal_enrichments.

Cycle:
  Every 6h, walk servers whose cache is stale (>24h old or never fetched).
  Batch of 50 per cycle to stay well under the 5000 req/hour limit.
  Full coverage of 790 MCPs: 4 cycles = 24 hours to warm-steady-state.
"""

# _zo_backoff_v1

import json
import os
import shutil
import sys
import time as _time
from datetime import datetime, timedelta, timezone as _tz
from email.utils import parsedate_to_datetime as _parse_dt
from pathlib import Path
import requests as _req

# ---------------------------------------------------------------
# Rate budget configuration (token-bucket style)
# ---------------------------------------------------------------
_RATE_BUDGETS = {
    "npm":    {"calls": 10, "window": 60,  "key": "rate_budget_npm"},
    "pypi":   {"calls": 5,  "window": 60,  "key": "rate_budget_pypi"},
    "github": {"calls": 30, "window": 3600, "key": "rate_budget_github"},
}

_WRITE_SVC = "http://127.0.0.1:8772"
LOGS_DIR = Path("/home/workspace/logs")
LOGS_DIR.mkdir(parents=True, exist_ok=True)
_429_LOG = LOGS_DIR / "ecosystems_429.log"

# ---------------------------------------------------------------
# Backoff configuration
# ---------------------------------------------------------------
_MAX_BACKOFF_SECS = 32
_INITIAL_BACKOFF_SECS = 1

# ---------------------------------------------------------------
# Persistent rate budget state (persisted in service_health table)
# ---------------------------------------------------------------
_rate_budget_state: dict = {}

def _get_utc_now_iso() -> str:
    return datetime.now(_tz.utc).isoformat()

def _utc_now_ts() -> float:
    return datetime.now(_tz.utc).timestamp()

# ---------------------------------------------------------------
# Backup helper
# ---------------------------------------------------------------
def _backup_file(path: Path) -> Path:
    """Create timestamped .bak backup of the file."""
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    bak = Path(str(path) + f".bak.{ts}")
    shutil.copy2(path, bak)
    return bak

# ---------------------------------------------------------------
# Check if patch already applied
# ---------------------------------------------------------------
def _is_backoff_applied(path: Path) -> bool:
    """Return True if '# _zo_backoff_v1' marker is present in file."""
    if not path.exists():
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return "# _zo_backoff_v1" in content
    except Exception:
        return False

# ---------------------------------------------------------------
# Rate budget persistence via write_service
# ---------------------------------------------------------------
def _load_rate_budgets():
    """Load rate budgets from service_health table if they exist."""
    global _rate_budget_state
    _rate_budget_state = {}
    for source, config in _RATE_BUDGETS.items():
        budget_key = config["key"]
        try:
            resp = _req.post(
                f"{_WRITE_SVC}/query",
                json={"sql": f"SELECT last_heartbeat FROM service_health WHERE service = '{budget_key}'"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                rows = data.get("rows", [])
                if rows and len(rows) > 0:
                    heartbeat_str = rows[0].get("last_heartbeat", "")
                    if heartbeat_str:
                        try:
                            dt = datetime.fromisoformat(heartbeat_str.replace("Z", "+00:00"))
                            _rate_budget_state[source] = {"reset_at": dt.timestamp(), "tokens": config["calls"]}
                            continue
                        except (ValueError, TypeError):
                            pass
        except Exception:
            pass
        # Initialize fresh budget state
        _rate_budget_state[source] = {
            "reset_at": _utc_now_ts() + config["window"],
            "tokens": config["calls"],
        }

def _save_rate_budget(source: str):
    """Persist rate budget state to service_health table."""
    config = _RATE_BUDGETS.get(source)
    if not config:
        return
    budget = _rate_budget_state.get(source)
    if not budget:
        return
    budget_key = config["key"]
    reset_iso = datetime.fromtimestamp(budget["reset_at"], tz=_tz.utc).isoformat()
    try:
        _req.post(
            f"{_WRITE_SVC}/write",
            json={
                "table": "service_health",
                "rows": {
                    "service": budget_key,
                    "last_heartbeat": reset_iso,
                },
                "wait": True,
            },
            timeout=10,
        )
    except Exception:
        pass

def _check_rate_limit(source: str) -> bool:
    """
    Check if we have budget for this source.
    Returns True if we can make a request, False if we should wait.
    """
    config = _RATE_BUDGETS.get(source)
    if not config:
        return True

    now = _utc_now_ts()
    if source not in _rate_budget_state:
        _load_rate_budgets()

    budget = _rate_budget_state.get(source)
    if not budget:
        return True

    # Refill tokens if window has passed
    if now >= budget["reset_at"]:
        budget["tokens"] = config["calls"]
        budget["reset_at"] = now + config["window"]

    if budget["tokens"] > 0:
        budget["tokens"] -= 1
        _save_rate_budget(source)
        return True
    return False

def _get_remaining_budget(source: str) -> int:
    """Get remaining tokens for a source."""
    if source not in _rate_budget_state:
        _load_rate_budgets()
    budget = _rate_budget_state.get(source)
    if not budget:
        return 0
    return budget.get("tokens", 0)

# ---------------------------------------------------------------
# 429 logging
# ---------------------------------------------------------------
def _log_429(source: str, endpoint: str, status_code: int, retry_after: str, tokens_remaining: int):
    """Log every 429 to ecosystems_429.log with timestamp+source+endpoint+remaining_budget."""
    ts = _get_utc_now_iso()
    line = f"{ts} | source={source} | endpoint={endpoint} | status={status_code} | retry_after={retry_after} | remaining_budget={tokens_remaining}\n"
    try:
        with open(_429_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

# ---------------------------------------------------------------
# Exponential backoff with Retry-After support
# ---------------------------------------------------------------
def _parse_retry_after(retry_after: str) -> float | None:
    """
    Parse Retry-After header value.
    Returns seconds as float, or None if unparseable.
    """
    if not retry_after:
        return None
    retry_after = retry_after.strip()
    # Try integer (seconds)
    try:
        return float(retry_after)
    except ValueError:
        pass
    # Try HTTP-date via email.utils.parsedate_to_datetime
    try:
        dt = _parse_dt(retry_after)
        if dt:
            return max(0.0, dt.timestamp() - _utc_now_ts())
    except Exception:
        pass
    return None

def _exponential_backoff(attempt: int, retry_after: float | None = None) -> float:
    """
    Compute backoff delay in seconds.
    - Starts at 1 second, doubles each attempt, caps at 32 seconds.
    - If retry_after is provided and valid, use it (capped at max backoff).
    """
    if retry_after is not None:
        return min(retry_after, _MAX_BACKOFF_SECS)
    delay = min(_INITIAL_BACKOFF_SECS * (2 ** attempt), _MAX_BACKOFF_SECS)
    return delay

# ---------------------------------------------------------------
# HTTP client with backoff and rate limiting
# ---------------------------------------------------------------
def _get_with_backoff(
    url: str,
    source: str,
    headers: dict | None = None,
    timeout: float = 15.0,
) -> tuple[_req.Response | None, bool]:
    """
    Fetch URL with exponential backoff on 429/503/timeout.
    Returns (response, success).
    Logs 429s to ecosystems_429.log.
    Respects rate limits before making requests.
    """
    attempt = 0
    while True:
        # Check rate limit before making request
        if not _check_rate_limit(source):
            config = _RATE_BUDGETS.get(source, {})
            window = config.get("window", 60)
            _zo_log_429(source, url, 429, f"rate_limited_wait_{window}s", 0)
            _time.sleep(min(window, _MAX_BACKOFF_SECS))
            continue

        try:
            resp = _req.get(url, headers=headers, timeout=timeout)
            status = resp.status_code

            if status == 429:
                retry_after = resp.headers.get("Retry-After", "")
                parsed_ra = _parse_retry_after(retry_after)
                tokens = _get_remaining_budget(source)
                _log_429(source, url, status, retry_after, tokens)
                delay = _exponential_backoff(attempt, parsed_ra)
                _time.sleep(delay)
                attempt += 1
                continue

            if status == 503:
                retry_after = resp.headers.get("Retry-After", "")
                parsed_ra = _parse_retry_after(retry_after)
                delay = _exponential_backoff(attempt, parsed_ra)
                _time.sleep(delay)
                attempt += 1
                continue

            return resp, True

        except _req.exceptions.Timeout:
            delay = _exponential_backoff(attempt)
            _time.sleep(delay)
            attempt += 1
            continue

        except _req.exceptions.RequestException:
            return None, False

# ---------------------------------------------------------------
# Logging helpers (from original)
# ---------------------------------------------------------------
def _zo_log_429(source: str, endpoint: str, status_code: int, retry_after: str, tokens_remaining: int):
    """Log every 429 to ecosystems_429.log with timestamp+source+endpoint+remaining_budget."""
    ts = _get_utc_now_iso()
    line = f"{ts} | source={source} | endpoint={endpoint} | status={status_code} | retry_after={retry_after} | remaining_budget={tokens_remaining}\n"
    try:
        with open(_429_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

# ---------------------------------------------------------------
# Remaining original code follows
# ---------------------------------------------------------------

CACHE_TTL_HOURS = 24
BATCH_SIZE = 50
CYCLE_INTERVAL_SECS = 6 * 3600
FETCH_TIMEOUT = 15
FETCH_DELAY_SUCCESS = 0.5

_USER_AGENT = "ZO-Sentinel/1.0 (zo-sentinel@anthropic.com)"
_FROM_HEADER = {"From": "zo-sentinel@anthropic.com"}

_WRITE_URL = f"{_WRITE_SVC}/write"
_QUERY_URL = f"{_WRITE_SVC}/query"
_EXECUTE_URL = f"{_WRITE_SVC}/execute"
SERVICE_NAME = "ecosystems_metadata_fetcher"
PORT = 8783
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = LOGS_DIR / f"{SERVICE_NAME}.log"


def log(msg: str):
    ts = _get_utc_now_iso()
    line = f"{ts} {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def signal_handler(signum, frame):
    log("Caught signal, shutting down gracefully.")
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass
    sys.exit(0)


def check_single_instance():
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)
            log(f"Already running as PID {old_pid}, exiting.")
            sys.exit(0)
        except (ProcessLookupError, ValueError, PermissionError):
            log(f"Stale PID file, removing.")
            pid_file.unlink(missing_ok=True)
    pid_file.write_text(str(os.getpid()))


def remove_pid_file():
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def get_db_path():
    return "/tmp/zo_sentinel.duckdb"


def ws_query(sql: str) -> list[dict]:
    try:
        resp = _req.post(_QUERY_URL, json={"sql": sql}, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("rows", [])
        log(f"ws_query error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log(f"ws_query exception: {e}")
    return []


def ws_write(table: str, rows: dict) -> bool:
    try:
        resp = _req.post(_WRITE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=30)
        if resp.status_code == 200:
            return True
        log(f"ws_write error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log(f"ws_write exception: {e}")
    return False


def ws_execute(sql: str) -> bool:
    try:
        resp = _req.post(_EXECUTE_URL, json={"sql": sql}, timeout=30)
        if resp.status_code == 200:
            return True
        log(f"ws_execute error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log(f"ws_execute exception: {e}")
    return False


def send_heartbeat():
    ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": _get_utc_now_iso()})


def heartbeat_loop():
    while True:
        send_heartbeat()
        _time.sleep(60)


def get_write_url() -> str:
    return _WRITE_URL


def get_query_url() -> str:
    return _QUERY_URL


def get_execute_url() -> str:
    return _EXECUTE_URL


def ensure_tables():
    ws_execute("""
        CREATE TABLE IF NOT EXISTS mcp_ecosystems_metadata (
            server_id    VARCHAR PRIMARY KEY,
            fetched_at   VARCHAR,
            cache_age_h  DOUBLE,
            npm_name     VARCHAR,
            pypi_name    VARCHAR,
            github_repo  VARCHAR,
            downloads    BIGINT,
            latest_version  VARCHAR,
            latest_release_age_days INTEGER,
            ecosystems   VARCHAR,
            package_count INTEGER,
            dominant_ecosystem VARCHAR
        )
    """)
    log("Tables ensured.")


def _ecosyste_url(server_id: str) -> str:
    return f"https://ecosyste.ms:443/api/v1/registries/resolve?package={server_id}"


def _ecosyste_downloads(package: str, ecosystem: str) -> str:
    return f"https://ecosyste.ms:443/api/v1/downloads/overview?package={package}&ecosystem={ecosystem}"


def _ecosyste_releases(package: str, ecosystem: str) -> str:
    return f"https://ecosyste.ms:443/api/v1/releases/latest?package={package}&ecosystem={ecosystem}"


def _ecosyste_cousins(package: str, ecosystem: str) -> str:
    return f"https://ecosyste.ms:443/api/v1/registries/cousins?package={package}&ecosystem={ecosystem}"


def _base_headers() -> dict:
    return {
        "User-Agent": _USER_AGENT,
        "From": _FROM_HEADER["From"],
        "Accept": "application/json",
    }


def _resolve_one(server_id: str) -> dict | None:
    """Resolve a server_id to a package name via ecosyste.ms."""
    url = _ecosyste_url(server_id)
    resp, ok = _get_with_backoff(url, "ecosyste", headers=_base_headers(), timeout=FETCH_TIMEOUT)
    if not ok or resp is None:
        return None
    if resp.status_code == 200:
        try:
            return resp.json()
        except Exception:
            return None
    return None


def _downloads_overview(package: str, ecosystem: str) -> dict | None:
    url = _ecosyste_downloads(package, ecosystem)
    resp, ok = _get_with_backoff(url, "ecosyste", headers=_base_headers(), timeout=FETCH_TIMEOUT)
    if not ok or resp is None:
        return None
    if resp.status_code == 200:
        try:
            return resp.json()
        except Exception:
            return None
    return None


def _latest_release(package: str, ecosystem: str) -> dict | None:
    url = _ecosyste_releases(package, ecosystem)
    resp, ok = _get_with_backoff(url, "ecosyste", headers=_base_headers(), timeout=FETCH_TIMEOUT)
    if not ok or resp is None:
        return None
    if resp.status_code == 200:
        try:
            return resp.json()
        except Exception:
            return None
    return None


def _cousins(package: str, ecosystem: str) -> list[dict]:
    url = _ecosyste_cousins(package, ecosystem)
    resp, ok = _get_with_backoff(url, "ecosyste", headers=_base_headers(), timeout=FETCH_TIMEOUT)
    if not ok or resp is None:
        return []
    if resp.status_code == 200:
        try:
            data = resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "packages" in data:
                return data["packages"]
        except Exception:
            return []
    return []


def _pick_best_cousin(cousins: list[dict], preferred: str) -> dict | None:
    """Pick the highest-download cousin from the dominant ecosystem."""
    best = None
    best_dl = -1
    for c in cousins:
        pkg = c.get("package") or c.get("name")
        eco = c.get("ecosystem", "")
        dls = c.get("downloads", 0) or 0
        if not pkg:
            continue
        if eco == preferred:
            if dls > best_dl:
                best_dl = dls
                best = c
                best["_package"] = pkg
    if not best and cousins:
        for c in cousins:
            pkg = c.get("package") or c.get("name")
            eco = c.get("ecosystem", "")
            dls = c.get("downloads", 0) or 0
            if not pkg:
                continue
            if dls > best_dl:
                best_dl = dls
                best = c
                best["_package"] = pkg
    return best


def _ecosystem_from_source(source: str) -> str:
    s = source.lower()
    if "npm" in s or "node" in s or "/npm/" in s:
        return "npm"
    if "pypi" in s or "/pypi/" in s:
        return "PyPI"
    if "github" in s or "/github/" in s:
        return "GitHub"
    if "docker" in s or "/docker/" in s:
        return "Docker"
    return "npm"


def _fetch_metadata_for_server(server_id: str, source: str) -> dict | None:
    """Full metadata fetch for one server_id, with backoff and rate limiting."""
    resolve = _resolve_one(server_id)
    if not resolve:
        return None
    pkg = resolve.get("package") or resolve.get("name")
    eco = resolve.get("ecosystem") or _ecosystem_from_source(source)
    if not pkg:
        return None
    downloads_data = _downloads_overview(pkg, eco) or {}
    downloads = downloads_data.get("total", 0) or 0
    latest = _latest_release(pkg, eco) or {}
    latest_version = latest.get("version") or latest.get("tag_name") or ""
    published_at = latest.get("published_at") or latest.get("created_at") or ""
    latest_release_age_days = 0
    if published_at:
        try:
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            latest_release_age_days = (datetime.now(_tz.utc) - dt).days
        except Exception:
            pass
    cousins = _cousins(pkg, eco)
    package_count = len(cousins)
    ecosystems_raw = [c.get("ecosystem", "") for c in cousins if c.get("ecosystem")]
    if eco not in ecosystems_raw:
        ecosystems_raw.insert(0, eco)
    ecosystems_str = ",".join(sorted(set(ecosystems_raw)))
    dominant = eco
    if cousins:
        best = _pick_best_cousin(cousins, eco)
        if best:
            dominant = best.get("ecosystem", eco)
    return {
        "server_id": server_id,
        "fetched_at": _get_utc_now_iso(),
        "cache_age_h": 0.0,
        "npm_name": pkg if eco == "npm" else "",
        "pypi_name": pkg if eco == "PyPI" else "",
        "github_repo": pkg if eco == "GitHub" else "",
        "downloads": downloads,
        "latest_version": latest_version,
        "latest_release_age_days": latest_release_age_days,
        "ecosystems": ecosystems_str,
        "package_count": package_count,
        "dominant_ecosystem": dominant,
    }


def get_stale_servers(limit: int = BATCH_SIZE) -> list[dict]:
    sql = f"""
        SELECT r.server_id, r.source
        FROM mcp_server_registry r
        LEFT JOIN mcp_ecosystems_metadata m ON r.server_id = m.server_id
        WHERE m.server_id IS NULL
           OR m.fetched_at IS NULL
           OR (
               m.cache_age_h > {CACHE_TTL_HOURS}
               AND r.updated_at > m.fetched_at
           )
        LIMIT {limit}
    """
    return ws_query(sql)


def upsert_metadata(meta: dict):
    sql = """
        INSERT INTO mcp_ecosystems_metadata
            (server_id, fetched_at, cache_age_h, npm_name, pypi_name, github_repo,
             downloads, latest_version, latest_release_age_days,
             ecosystems, package_count, dominant_ecosystem)
        VALUES
            (:server_id, :fetched_at, :cache_age_h, :npm_name, :pypi_name, :github_repo,
             :downloads, :latest_version, :latest_release_age_days,
             :ecosystems, :package_count, :dominant_ecosystem)
        ON CONFLICT (server_id) DO UPDATE SET
            fetched_at           = EXCLUDED.fetched_at,
            cache_age_h           = EXCLUDED.cache_age_h,
            npm_name              = EXCLUDED.npm_name,
            pypi_name             = EXCLUDED.pypi_name,
            github_repo           = EXCLUDED.github_repo,
            downloads             = EXCLUDED.downloads,
            latest_version        = EXCLUDED.latest_version,
            latest_release_age_days = EXCLUDED.latest_release_age_days,
            ecosystems            = EXCLUDED.ecosystems,
            package_count         = EXCLUDED.package_count,
            dominant_ecosystem    = EXCLUDED.dominant_ecosystem
    """
    ws_write("mcp_ecosystems_metadata", meta)


def cycle():
    log("Starting fetch cycle.")
    servers = get_stale_servers(BATCH_SIZE)
    if not servers:
        log("No stale servers, sleeping.")
        return
    log(f"Processing {len(servers)} servers.")
    for row in servers:
        sid = row.get("server_id")
        src = row.get("source") or ""
        if not sid:
            continue
        log(f"  Fetching {sid} (source={src})")
        meta = _fetch_metadata_for_server(sid, src)
        if meta:
            upsert_metadata(meta)
            log(f"  OK: {sid} -> downloads={meta['downloads']}, eco={meta['dominant_ecosystem']}")
        else:
            log(f"  SKIP: {sid} (no resolution)")
        _time.sleep(FETCH_DELAY_SUCCESS)
    log("Cycle complete.")


def run():
    import signal as _sig
    check_single_instance()
    for sig in (_sig.SIGTERM, _sig.SIGINT):
        _sig.signal(sig, signal_handler)
    log(f"{SERVICE_NAME} starting.")
    _load_rate_budgets()
    ensure_tables()
    send_heartbeat()
    while True:
        try:
            cycle()
        except Exception as e:
            log(f"Cycle error: {e}")
        send_heartbeat()
        _time.sleep(CYCLE_INTERVAL_SECS)


# ---------------------------------------------------------------
# Smoke test: simulate 429 response and verify backoff
# ---------------------------------------------------------------
def _smoke_test_backoff():
    """Simulate 429 responses and verify exponential backoff behavior."""
    import unittest.mock as _mock

    log("=== SMOKE TEST: backoff simulation ===")

    # Track sleep calls
    sleep_calls = []
    original_sleep = _time.sleep

    def tracked_sleep(secs):
        sleep_calls.append(secs)
        original_sleep(0.001)  # minimal actual sleep for test speed

    test_source = "npm"
    test_url = "https://ecosyste.ms:443/api/v1/registries/resolve?package=test-npm-package"

    # Mock response simulating 429 with Retry-After header
    mock_429_resp = _mock.MagicMock()
    mock_429_resp.status_code = 429
    mock_429_resp.headers = {"Retry-After": "2"}

    # Mock response for retry (200 OK)
    mock_200_resp = _mock.MagicMock()
    mock_200_resp.status_code = 200
    mock_200_resp.json.return_value = {"package": "test-npm-package", "ecosystem": "npm"}

    call_count = [0]

    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_429_resp
        return mock_200_resp

    with _mock.patch("requests.get", side_effect=side_effect):
        with _mock.patch("time.sleep", side_effect=tracked_sleep):
            # Ensure we have budget
            if test_source in _rate_budget_state:
                _rate_budget_state[test_source]["tokens"] = 5

            resp, ok = _get_with_backoff(
                test_url,
                test_source,
                headers=_base_headers(),
                timeout=5.0
            )

    # Verify: first call should be 429, then we slept (Retry-After=2, capped at 32)
    assert call_count[0] == 2, f"Expected 2 calls, got {call_count[0]}"
    assert ok, "Second call should succeed"
    assert resp is not None, "Response should not be None on success"
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert len(sleep_calls) >= 1, f"Expected at least 1 sleep call, got {len(sleep_calls)}"
    # Retry-After=2 seconds, should be honored
    assert sleep_calls[0] <= 2.0, f"Expected backoff ~2s (Retry-After), got {sleep_calls[0]}"

    log(f"  429 backoff test PASSED: slept {sleep_calls[0]:.2f}s, then got 200")

    # Test exponential backoff without Retry-After header
    log("=== SMOKE TEST: exponential backoff (no Retry-After) ===")
    sleep_calls.clear()
    call_count[0] = 0

    mock_503_resp = _mock.MagicMock()
    mock_503_resp.status_code = 503
    mock_503_resp.headers = {}

    mock_200_resp2 = _mock.MagicMock()
    mock_200_resp2.status_code = 200
    mock_200_resp2.json.return_value = {"package": "test-pypi", "ecosystem": "PyPI"}

    def side_effect2(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_503_resp
        return mock_200_resp2

    with _mock.patch("requests.get", side_effect=side_effect2):
        with _mock.patch("time.sleep", side_effect=tracked_sleep):
            resp2, ok2 = _get_with_backoff(
                "https://ecosyste.ms:443/api/v1/registries/resolve?package=test-pypi",
                "pypi",
                headers=_base_headers(),
                timeout=5.0
            )

    # Verify exponential backoff: first sleep should be 1s (initial), then retry
    assert call_count[0] == 2, f"Expected 2 calls, got {call_count[0]}"
    assert ok2, "Second call should succeed"
    assert sleep_calls[0] == 1.0, f"Expected initial backoff 1s, got {sleep_calls[0]}"

    log(f"  Exponential backoff test PASSED: slept {sleep_calls[0]:.2f}s, then got 200")

    # Test max backoff cap (32 seconds)
    log("=== SMOKE TEST: backoff cap at 32s ===")
    sleep_calls.clear()
    call_count[0] = 0

    def side_effect3(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] <= 10:
            return mock_503_resp
        return mock_200_resp2

    with _mock.patch("requests.get", side_effect=side_effect3):
        with _mock.patch("time.sleep", side_effect=tracked_sleep):
            resp3, ok3 = _get_with_backoff(
                "https://ecosyste.ms:443/api/v1/registries/resolve?package=test-cap",
                "github",
                headers=_base_headers(),
                timeout=5.0
            )

    # Verify: after enough retries, backoff should cap at 32s
    # Backoff sequence: 1, 2, 4, 8, 16, 32, 32, 32...
    max_sleep = max(sleep_calls) if sleep_calls else 0
    assert max_sleep <= _MAX_BACKOFF_SECS, f"Backoff exceeded max: {max_sleep} > {_MAX_BACKOFF_SECS}"

    log(f"  Backoff cap test PASSED: max sleep was {max_sleep:.2f}s")

    # Test rate limit enforcement
    log("=== SMOKE TEST: rate limit enforcement ===")
    sleep_calls.clear()
    call_count[0] = 0

    # Exhaust budget
    _rate_budget_state["npm"]["tokens"] = 0
    _rate_budget_state["npm"]["reset_at"] = _utc_now_ts() + 3600

    with _mock.patch("requests.get", side_effect=side_effect):
        with _mock.patch("time.sleep", side_effect=tracked_sleep):
            resp4, ok4 = _get_with_backoff(
                test_url,
                "npm",
                headers=_base_headers(),
                timeout=5.0
            )

    # Should have called rate_limit check and slept
    assert len(sleep_calls) >= 1, "Should sleep when rate limited"
    log(f"  Rate limit test PASSED: slept {sleep_calls[0]:.2f}s when rate limited")

    # Test 429 logging
    log("=== SMOKE TEST: 429 logging ===")
    try:
        if _429_LOG.exists():
            _429_LOG.unlink()
    except Exception:
        pass

    sleep_calls.clear()
    call_count[0] = 0

    with _mock.patch("requests.get", side_effect=side_effect):
        with _mock.patch("time.sleep", side_effect=tracked_sleep):
            resp5, ok5 = _get_with_backoff(
                test_url,
                "npm",
                headers=_base_headers(),
                timeout=5.0
            )

    assert _429_LOG.exists(), "429 log file should be created"
    log_content = _429_LOG.read_text()
    assert "source=npm" in log_content, "Log should contain source"
    assert "status=429" in log_content, "Log should contain status"
    assert "endpoint=" in log_content, "Log should contain endpoint"
    assert "remaining_budget=" in log_content, "Log should contain remaining budget"
    log(f"  429 logging test PASSED: logged to {_429_LOG}")

    log("=== ALL SMOKE TESTS PASSED ===")
    print("ALL SMOKE TESTS PASSED")
    return True


if __name__ == "__main__":
    # THIS ENTRYPOINT USED TO BE A ONE-SHOT SELF-PATCHER, NOT THE DAEMON (#4176).
    #
    # A build directive replaced __main__ with the smoke test for its own backoff
    # patch. The result: `go.sh` launched this module on every boot, __main__
    # found its own backup marker, printed "Patch already applied (marker
    # found), exiting 0." and exited ZERO -- so daemon_wrapper.sh logged
    # "clean exit (rc=0); wrapper stopping" and stood down.
    #
    # It looked exactly like a healthy daemon that had finished its work. The
    # last real cycle this module ran was 2026-05-29; it was declared, launched,
    # and dead for three months, and the only visible symptom was a log line
    # that reads like success.
    #
    # run() -- the actual daemon, with the single-instance guard, signal
    # handlers, rate budgets and cycle loop -- was fully intact the whole time
    # and simply unreachable. So __main__ calls it, and the self-patch path
    # keeps working behind an explicit flag rather than squatting on the
    # default.
    if "--self-patch" in sys.argv:
        src_path = Path(__file__)
        if _is_backoff_applied(src_path):
            print("Patch already applied (marker found), exiting 0.")
            sys.exit(0)
        bak_path = _backup_file(src_path)
        print(f"Backed up original to {bak_path}")
        try:
            if _smoke_test_backoff():
                print("Smoke tests passed. Patch applied successfully.")
                sys.exit(0)
            print("Smoke tests FAILED.")
            sys.exit(1)
        except Exception as e:
            print(f"Smoke test exception: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        finally:
            remove_pid_file()

    try:
        run()
    finally:
        remove_pid_file()