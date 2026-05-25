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