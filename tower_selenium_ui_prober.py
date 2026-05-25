import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
except ImportError:
    print("FATAL: selenium package not installed. Run: pip install selenium")
    sys.exit(2)

try:
    import requests
except ImportError:
    print("FATAL: requests package not installed. Run: pip install requests")
    sys.exit(2)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def probe_http_status(target_url: str, timeout: int = 10) -> Optional[int]:
    """GET http status via requests (Selenium doesn't expose status cleanly)."""
    try:
        resp = requests.head(target_url, timeout=timeout, allow_redirects=True)
        return resp.status_code
    except Exception:
        try:
            resp = requests.get(target_url, timeout=timeout, allow_redirects=True)
            return resp.status_code
        except Exception:
            return None


def extract_security_headers(url: str, timeout: int = 10) -> Dict[str, Optional[str]]:
    """Extract 5 security headers via requests - missing keys reported as null."""
    headers = {
        'Strict-Transport-Security': None,
        'Content-Security-Policy': None,
        'X-Frame-Options': None,
        'X-Content-Type-Options': None,
        'Referrer-Policy': None
    }
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True)
        for key in headers:
            if key in resp.headers:
                headers[key] = resp.headers[key]
    except Exception:
        pass
    return headers


def probe_openapi_unauthed(url: str, timeout: int = 10) -> Optional[int]:
    """GET /openapi.json without auth and record status code."""
    openapi_url = url.rstrip('/') + '/openapi.json'
    try:
        resp = requests.get(openapi_url, timeout=timeout)
        return resp.status_code
    except Exception:
        return None


def get_console_logs(driver) -> Dict[str, int]:
    """Collect browser console entries and count by level."""
    counts = {'SEVERE': 0, 'WARNING': 0, 'INFO': 0}
    try:
        logs = driver.get_log('browser')
        for entry in logs:
            level = entry.get('level', 'INFO').upper()
            if level in counts:
                counts[level] += 1
    except Exception:
        pass
    return counts


def probe_dom_content_loaded(driver) -> Optional[int]:
    """Measure DOMContentLoaded via performance timing."""
    try:
        result = driver.execute_script(
            'return performance.timing.domContentLoadedEventEnd - performance.timing.navigationStart'
        )
        return int(result) if result else None
    except Exception:
        return None


def run_probe(target_url: str, output_dir: str) -> Dict[str, Any]:
    """Execute full UI probe against target URL."""
    probe_ts = utc_now_iso()
    errors: List[str] = []
    
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    # Determine screenshot and JSON paths
    ts_slug = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')[:-3] + 'Z'
    screenshot_path = os.path.join(output_dir, f'ui_probe_{ts_slug}.png')
    json_path = os.path.join(output_dir, f'ui_probe_{ts_slug}.json')
    
    result = {
        'probe_ts_utc': probe_ts,
        'target_url': target_url,
        'http_status': None,
        'security_headers': {
            'Strict-Transport-Security': None,
            'Content-Security-Policy': None,
            'X-Frame-Options': None,
            'X-Content-Type-Options': None,
            'Referrer-Policy': None
        },
        'openapi_unauthed_status': None,
        'dom_content_loaded_ms': None,
        'browser_console': {'SEVERE': 0, 'WARNING': 0, 'INFO': 0},
        'screenshot_path': None,
        'errors': []
    }
    
    driver = None
    try:
        # Step 1: Get HTTP status via requests (separate from Selenium)
        print(f"[PROBE] Fetching HTTP status for: {target_url}")
        result['http_status'] = probe_http_status(target_url)
        print(f"[PROBE] HTTP status: {result['http_status']}")
        
        # Step 2: Extract security headers
        print(f"[PROBE] Extracting security headers")
        result['security_headers'] = extract_security_headers(target_url)
        
        # Step 3: Probe /openapi.json unauthed
        print(f"[PROBE] Probing /openapi.json unauthed")
        result['openapi_unauthed_status'] = probe_openapi_unauthed(target_url)
        print(f"[PROBE] OpenAPI status: {result['openapi_unauthed_status']}")
        
        # Step 4: Start headless Chrome with required options
        print(f"[PROBE] Starting headless Chrome")
        options = Options()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1440,900')
        options.add_argument('--disable-gpu')
        options.add_argument('--ignore-certificate-errors')
        
        driver = webdriver.Chrome(options=options)
        
        # Step 5: GET root path with 30s timeout
        print(f"[PROBE] Loading page (30s timeout)")
        driver.set_page_load_timeout(30)
        driver.get(target_url)
        
        # Step 6: Wait for page to stabilize
        try:
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script('return document.readyState') == 'complete'
            )
        except Exception as e:
            errors.append(f"Page stabilization wait failed: {str(e)}")
        
        # Step 7: Measure DOMContentLoaded
        print(f"[PROBE] Measuring DOMContentLoaded")
        result['dom_content_loaded_ms'] = probe_dom_content_loaded(driver)
        print(f"[PROBE] DOMContentLoaded: {result['dom_content_loaded_ms']}ms")
        
        # Step 8: Collect browser console logs
        print(f"[PROBE] Collecting browser console logs")
        result['browser_console'] = get_console_logs(driver)
        
        # Step 9: Take full-page screenshot
        print(f"[PROBE] Taking full-page screenshot")
        driver.save_screenshot(screenshot_path)
        result['screenshot_path'] = screenshot_path
        print(f"[PROBE] Screenshot saved: {screenshot_path}")
        
    except Exception as e:
        error_msg = f"Selenium/Chrome error: {str(e)}"
        errors.append(error_msg)
        print(f"[PROBE] ERROR: {error_msg}")
        
        # Attempt screenshot even on error
        if driver:
            try:
                driver.save_screenshot(screenshot_path)
                result['screenshot_path'] = screenshot_path
            except Exception:
                pass
        
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
    
    result['errors'] = errors
    return result


def write_report(result: Dict[str, Any], output_dir: str) -> str:
    """Write probe result to JSON file, return path."""
    ts_slug = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')[:-3] + 'Z'
    json_path = os.path.join(output_dir, f'ui_probe_{ts_slug}.json')
    
    os.makedirs(output_dir, exist_ok=True)
    
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    return json_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Tower-side Selenium UI prober for Sentinel dashboard'
    )
    parser.add_argument(
        '--target',
        default='https://zo-sentinel-ui-robinc.zocomputer.io',
        help='Target URL to probe (default: https://zo-sentinel-ui-robinc.zocomputer.io)'
    )
    parser.add_argument(
        '--output-dir',
        default='C:/Users/robin/ZoComputer/shared/outputs/probes',
        help='Output directory for JSON report and screenshot (default: C:/Users/robin/ZoComputer/shared/outputs/probes)'
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    
    target_url = args.target
    output_dir = args.output_dir.replace('\\', '/')  # Python handles forward slashes on Windows
    
    print(f"=" * 60)
    print(f"Sentinel UI Selenium Prober")
    print(f"=" * 60)
    print(f"Target: {target_url}")
    print(f"Output: {output_dir}")
    print(f"Started: {utc_now_iso()}")
    print(f"=" * 60)
    
    try:
        result = run_probe(target_url, output_dir)
    except Exception as e:
        error_msg = f"Unexpected probe failure: {str(e)}"
        print(f"[PROBE] FATAL: {error_msg}")
        result = {
            'probe_ts_utc': utc_now_iso(),
            'target_url': target_url,
            'http_status': None,
            'security_headers': {
                'Strict-Transport-Security': None,
                'Content-Security-Policy': None,
                'X-Frame-Options': None,
                'X-Content-Type-Options': None,
                'Referrer-Policy': None
            },
            'openapi_unauthed_status': None,
            'dom_content_loaded_ms': None,
            'browser_console': {'SEVERE': 0, 'WARNING': 0, 'INFO': 0},
            'screenshot_path': None,
            'errors': [error_msg]
        }
    
    json_path = write_report(result, output_dir)
    
    print(f"=" * 60)
    print(f"Probe Complete: {utc_now_iso()}")
    print(f"Report: {json_path}")
    print(f"Screenshot: {result.get('screenshot_path', 'N/A')}")
    print(f"HTTP Status: {result.get('http_status', 'N/A')}")
    print(f"OpenAPI Status: {result.get('openapi_unauthed_status', 'N/A')}")
    print(f"DOM Content Loaded: {result.get('dom_content_loaded_ms', 'N/A')}ms")
    print(f"Console SEVERE: {result.get('browser_console', {}).get('SEVERE', 0)}")
    print(f"Console WARNING: {result.get('browser_console', {}).get('WARNING', 0)}")
    print(f"Console INFO: {result.get('browser_console', {}).get('INFO', 0)}")
    print(f"Errors: {len(result.get('errors', []))}")
    
    if result.get('errors'):
        for err in result['errors']:
            print(f"  - {err}")
    
    print(f"=" * 60)
    
    # Exit code: 0 success, 1 probe failure (non-fatal errors), 2 bad CLI (handled by argparse)
    if result.get('errors'):
        print(f"Exit code: 1 (probe completed with errors)")
        return 1
    else:
        print(f"Exit code: 0 (probe completed successfully)")
        return 0


if __name__ == '__main__':
    sys.exit(main())