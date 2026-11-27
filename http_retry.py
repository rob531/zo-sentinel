import requests
import time
from typing import Optional


def get_with_retry(url, params=None, headers=None, retries=3, backoff=2.0, timeout=10):
    """GET with automatic retry and exponential backoff.
    
    Args:
        url: The URL to request
        params: Optional query parameters
        headers: Optional request headers
        retries: Number of retry attempts (default 3)
        backoff: Exponential backoff multiplier (default 2.0)
        timeout: Request timeout in seconds (default 10)
    
    Returns:
        requests.Response object or None if all retries exhausted
    """
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code >= 500:
                if attempt < retries:
                    wait = backoff ** attempt
                    print(f"Attempt {attempt}/{retries} failed with {response.status_code}, retrying in {wait}s...")
                    time.sleep(wait)
                continue
            return response
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < retries:
                wait = backoff ** attempt
                print(f"Attempt {attempt}/{retries} failed with {type(e).__name__}, retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"All {retries} retries exhausted for {url}")
                return None
    
    print(f"All {retries} retries exhausted for {url}")
    return None


def post_with_retry(url, json=None, headers=None, retries=3, backoff=2.0, timeout=10):
    """POST with automatic retry and exponential backoff.
    
    Args:
        url: The URL to request
        json: Optional JSON payload
        headers: Optional request headers
        retries: Number of retry attempts (default 3)
        backoff: Exponential backoff multiplier (default 2.0)
        timeout: Request timeout in seconds (default 10)
    
    Returns:
        requests.Response object or None if all retries exhausted
    """
    for attempt in range(1, retries + 1):
        try:
            response = requests.post(url, json=json, headers=headers, timeout=timeout)
            if response.status_code >= 500:
                if attempt < retries:
                    wait = backoff ** attempt
                    print(f"Attempt {attempt}/{retries} failed with {response.status_code}, retrying in {wait}s...")
                    time.sleep(wait)
                continue
            return response
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < retries:
                wait = backoff ** attempt
                print(f"Attempt {attempt}/{retries} failed with {type(e).__name__}, retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"All {retries} retries exhausted for {url}")
                return None
    
    print(f"All {retries} retries exhausted for {url}")
    return None


def safe_json(response) -> Optional[dict]:
    """Safely parse JSON from response.
    
    Args:
        response: requests.Response object
    
    Returns:
        Parsed JSON dict or None on decode error
    """
    try:
        return response.json()
    except Exception:
        return None

# ---------------------------------------------------------------------------
# GR-4 (2026-11-27): request_with_retry -- the drop-in for a bare requests call.
#
# get_/post_with_retry return None when retries run out, so a call site written
# as `r = requests.post(...); r.raise_for_status()` cannot adopt them without
# changing what failure looks like. This keeps the bare call's contract: it
# returns the last Response, or re-raises the last exception.
#
# Retrying a WRITE that may already have been applied is not safe, so a call is
# non-idempotent unless the caller says otherwise. For those, only a
# ConnectionError (the request never reached a listener -- the WriteService
# bounce during `zm go`) is retried. Reads (idempotent=True) also retry on
# Timeout, 5xx and 429, honouring a numeric Retry-After up to max_wait.
# ---------------------------------------------------------------------------
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def request_with_retry(method, url, *, idempotent=False, retries=3, backoff=1.0,
                       max_wait=30.0, sleep=time.sleep, session=None, **kwargs):
    send = (session or requests).request
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = send(method, url, **kwargs)
        except requests.exceptions.ConnectionError as e:
            last_exc = e
        except requests.exceptions.Timeout as e:
            if not idempotent:
                raise
            last_exc = e
        else:
            if not (idempotent and resp.status_code in RETRYABLE_STATUS) or attempt == retries:
                return resp
            last_exc = None
            ra = resp.headers.get("Retry-After", "") if resp.headers else ""
            if ra.strip().isdigit():
                sleep(min(float(ra), max_wait))
                continue
        if attempt == retries:
            break
        sleep(min(backoff * (2 ** (attempt - 1)), max_wait))
    if last_exc is not None:
        raise last_exc
    return resp
