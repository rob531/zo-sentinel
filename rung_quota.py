"""rung_quota.py -- file-based, best-effort per-rung quota tracking so the ladder
can SKIP a rung BEFORE its 429 (proactive failover) and PARK a rung that just
rate-limited (auto-recovers when the window resets -- e.g. MiniMax's daily token
bucket refills next day). Reads providers' rate-limit headers when present;
otherwise parks on 429. NEVER raises on the hot path.

Keyed by model_id (unique per ladder rung). Pure helpers (parse_headers,
_parse_retry, available-from-entry) are unit-tested without IO.
"""
import json
import os
import re
import time
from pathlib import Path

QUOTA_FILE = Path(os.environ.get("RUNG_QUOTA_FILE",
                  "/home/workspace/zo_sentinel_state/rung_quota.json"))
MIN_FRACTION = float(os.environ.get("RUNG_QUOTA_MIN_FRACTION", "0.08"))
DEFAULT_PARK_S = float(os.environ.get("RUNG_QUOTA_PARK_S", "300"))

# remaining / limit header families across providers (lowercased); first match wins.
_REMAIN = ("x-ratelimit-remaining-requests-day", "x-ratelimit-remaining-requests",
           "x-ratelimit-remaining-req-minute", "anthropic-ratelimit-requests-remaining")
_LIMIT = ("x-ratelimit-limit-requests-day", "x-ratelimit-limit-requests",
          "x-ratelimit-limit-req-minute", "anthropic-ratelimit-requests-limit")


def _load():
    try:
        return json.loads(QUOTA_FILE.read_text())
    except Exception:
        return {}


def _save(d):
    try:
        QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = QUOTA_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(QUOTA_FILE)
    except Exception:
        pass


def _hget(headers, names):
    low = {k.lower(): v for k, v in headers.items()}
    for n in names:
        if n in low:
            return low[n]
    return None


def parse_headers(headers):
    """-> {'remaining':int,'limit':int,'fraction':float} or {} if no usable gauge."""
    rem, lim = _hget(headers, _REMAIN), _hget(headers, _LIMIT)
    try:
        rem, lim = int(float(rem)), int(float(lim))
        if lim > 0:
            return {"remaining": rem, "limit": lim, "fraction": round(rem / lim, 4)}
    except (TypeError, ValueError):
        pass
    return {}


def _parse_retry(v):
    """Retry-After: seconds (int) or duration like '1.2s'/'220ms'/'2m' -> seconds."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    m = re.match(r"([\d.]+)\s*(ms|s|m)?", str(v).strip())
    if m:
        n, u = float(m.group(1)), m.group(2)
        return n / 1000 if u == "ms" else n * 60 if u == "m" else n
    return None


def _entry_available(e, now, min_fraction):
    if not e:
        return True, ""
    if e.get("park_until", 0) > now:
        return False, f"parked {int(e['park_until'] - now)}s"
    if "fraction" in e and e["fraction"] < min_fraction:
        return False, f"low {e['fraction']:.0%}"
    return True, ""


def record(rung_id, headers=None, status=200, retry_after=None):
    """Capture a call's quota signal. Best-effort; never raises."""
    try:
        d = _load()
        now = time.time()
        e = d.get(rung_id, {})
        if headers:
            g = parse_headers(headers)
            if g:
                e.update(g)
        e["ts"] = now
        if status == 429:
            e["park_until"] = now + (_parse_retry(retry_after) or DEFAULT_PARK_S)
        d[rung_id] = e
        _save(d)
    except Exception:
        pass


def park(rung_id, seconds=None):
    """Cool a rung down (e.g. it returned a rate-limit error). Auto-recovers."""
    try:
        d = _load()
        e = d.get(rung_id, {})
        e["park_until"] = time.time() + (seconds if seconds is not None else DEFAULT_PARK_S)
        e["ts"] = time.time()
        d[rung_id] = e
        _save(d)
    except Exception:
        pass


def available(rung_id, min_fraction=MIN_FRACTION):
    """(ok, reason). Unknown rung -> available (never block on missing data)."""
    try:
        return _entry_available(_load().get(rung_id), time.time(), min_fraction)
    except Exception:
        return True, ""


def snapshot():
    """All entries (for pipeline-watch fuel gauge). Adds live 'parked' flag."""
    d = _load()
    now = time.time()
    for e in d.values():
        e["parked_now"] = e.get("park_until", 0) > now
    return d
