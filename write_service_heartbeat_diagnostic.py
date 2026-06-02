import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Check psutil availability
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Check requests availability
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# Constants
API_ENDPOINT = "http://127.0.0.1:8772/health"
API_QUERY_ENDPOINT = "http://127.0.0.1:8772/query"
HEARTBEAT_THRESHOLD = 300
TIMEOUT = 10
MAX_EXCEPTIONS = 50
MAX_EXCEPTIONS_PER_FILE = 10
LOG_PATTERNS = ["Exception", "Error", "Traceback"]


def get_timestamp() -> str:
    """Get current UTC timestamp in ISO8601 format."""
    return datetime.now(timezone.utc).isoformat()


def check_process_liveness() -> Dict[str, Any]:
    """Check if write_service process is running and healthy."""
    result = {
        "pid_exists": False,
        "is_running": False,
        "is_zombie": False,
        "cpu_percent": 0.0,
        "memory_mb": 0.0,
        "psutil_available": PSUTIL_AVAILABLE,
        "error": None
    }

    try:
        if PSUTIL_AVAILABLE:
            matching_procs = []
            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'status', 'create_time']):
                try:
                    info = proc.info
                    name = (info.get('name') or '').lower()
                    cmdline_list = info.get('cmdline') or []
                    cmdline = ' '.join(cmdline_list).lower()

                    # Match: "write_service" in name or cmdline
                    # Also match uvicorn running write_service
                    if 'write_service' in name or 'write_service' in cmdline:
                        matching_procs.append(proc)
                    elif 'uvicorn' in name and 'write_service' in cmdline:
                        matching_procs.append(proc)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

            if matching_procs:
                proc = matching_procs[0]
                result["pid_exists"] = True
                result["is_running"] = True
                try:
                    status = proc.status()
                    result["is_zombie"] = status == psutil.STATUS_ZOMBIE
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    result["is_zombie"] = False
                    result["is_running"] = False

                if result["is_running"] and not result["is_zombie"]:
                    try:
                        result["cpu_percent"] = round(proc.cpu_percent(interval=0.5), 2)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        result["cpu_percent"] = 0.0
                    try:
                        result["memory_mb"] = round(proc.memory_info().rss / (1024 * 1024), 2)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        result["memory_mb"] = 0.0
        else:
            # Fallback to pgrep if psutil unavailable
            try:
                output = subprocess.check_output(
                    ["pgrep", "-f", "write_service"],
                    timeout=5,
                    stderr=subprocess.DEVNULL
                )
                pids = [int(p) for p in output.decode().strip().split() if p.isdigit()]
                result["pid_exists"] = len(pids) > 0
                result["is_running"] = result["pid_exists"]
            except subprocess.TimeoutExpired:
                result["error"] = "pgrep timed out"
            except subprocess.CalledProcessError:
                # pgrep returns non-zero when no matches found
                result["pid_exists"] = False
                result["is_running"] = False
            except FileNotFoundError:
                result["error"] = "pgrep not available"
    except Exception as e:
        result["error"] = str(e)

    return result


def check_api_responsiveness() -> Dict[str, Any]:
    """Check if the write_service API health endpoint is responsive."""
    result = {
        "endpoint": API_ENDPOINT,
        "reachable": False,
        "status_code": None,
        "response_time_ms": None,
        "error": None
    }

    if not REQUESTS_AVAILABLE:
        result["error"] = "requests library not available"
        return result

    try:
        start = time.time()
        response = requests.get(API_ENDPOINT, timeout=5)
        elapsed_ms = round((time.time() - start) * 1000, 2)

        result["reachable"] = True
        result["status_code"] = response.status_code
        result["response_time_ms"] = elapsed_ms
    except requests.exceptions.Timeout:
        result["error"] = "Request timed out after 5s"
    except requests.exceptions.ConnectionError as e:
        result["error"] = f"Connection refused: {str(e)[:100]}"
    except requests.exceptions.RequestException as e:
        result["error"] = f"Request failed: {str(e)[:100]}"
    except Exception as e:
        result["error"] = str(e)

    return result


def check_database_heartbeat() -> Dict[str, Any]:
    """Query the service_health table for last heartbeat timestamp."""
    result = {
        "last_heartbeat_timestamp": None,
        "heartbeat_record_count": None,
        "query_error": None
    }

    if not REQUESTS_AVAILABLE:
        result["query_error"] = "requests library not available"
        return result

    try:
        # Query for latest heartbeat timestamp
        payload = {
            "sql": "SELECT timestamp FROM service_health ORDER BY timestamp DESC LIMIT 1",
            "params": []
        }
        response = requests.post(API_QUERY_ENDPOINT, json=payload, timeout=TIMEOUT)

        if response.status_code == 200:
            try:
                data = response.json()
                if data and isinstance(data, list) and len(data) > 0:
                    result["last_heartbeat_timestamp"] = data[0][0] if data[0] else None
            except (ValueError, KeyError, IndexError):
                # Try alternative parsing
                text = response.text
                if text and text.strip():
                    result["last_heartbeat_timestamp"] = text.strip()

        # Also get count if possible
        try:
            count_payload = {
                "sql": "SELECT COUNT(*) FROM service_health WHERE name = 'write_service'",
                "params": []
            }
            count_resp = requests.post(API_QUERY_ENDPOINT, json=count_payload, timeout=TIMEOUT)
            if count_resp.status_code == 200:
                count_data = count_resp.json()
                if count_data and isinstance(count_data, list) and len(count_data) > 0:
                    result["heartbeat_record_count"] = count_data[0][0] if count_data[0] else 0
        except Exception:
            pass  # Count is optional
    except requests.exceptions.Timeout:
        result["query_error"] = "Query timed out after 10s"
    except requests.exceptions.ConnectionError:
        result["query_error"] = "Cannot connect to API"
    except requests.exceptions.RequestException as e:
        result["query_error"] = f"Query failed: {str(e)[:100]}"
    except Exception as e:
        result["query_error"] = str(e)

    return result


def scan_logs_for_exceptions() -> Dict[str, Any]:
    """Scan log files for recent exceptions."""
    result = {
        "log_files_found": [],
        "recent_exceptions": [],
        "check_error": None
    }

    # Common log locations to check
    log_paths = [
        "/var/log/write_service/",
        "/var/log/write_service",
        "./logs/",
        "./logs",
        ".",
    ]

    seen_exceptions = set()
    all_exceptions: List[str] = []

    try:
        for log_path in log_paths:
            try:
                import os
                if not os.path.exists(log_path):
                    continue

                # Get list of log files
                log_files = []
                if os.path.isdir(log_path):
                    for entry in os.listdir(log_path):
                        full_path = os.path.join(log_path, entry)
                        if os.path.isfile(full_path):
                            log_files.append(full_path)
                else:
                    log_files = [log_path]

                for log_file in log_files:
                    if log_file.endswith(('.log', '.txt', '')):
                        result["log_files_found"].append(log_file)

                        file_exceptions: List[str] = []
                        try:
                            with open(log_file, 'r', errors='ignore') as f:
                                lines = f.readlines()
                                # Get last 500 lines for efficiency
                                recent_lines = lines[-500:] if len(lines) > 500 else lines

                                for line in recent_lines:
                                    for pattern in LOG_PATTERNS:
                                        if pattern in line:
                                            # Deduplicate by line content
                                            line_stripped = line.strip()
                                            if line_stripped and line_stripped not in seen_exceptions:
                                                seen_exceptions.add(line_stripped)
                                                file_exceptions.append(line_stripped)
                                            break
                        except (IOError, OSError, UnicodeDecodeError):
                            continue

                        # Add exceptions from this file (limit per file)
                        all_exceptions.extend(file_exceptions[:MAX_EXCEPTIONS_PER_FILE])

                        # Stop if we've hit the global limit
                        if len(all_exceptions) >= MAX_EXCEPTIONS:
                            break

                if len(all_exceptions) >= MAX_EXCEPTIONS:
                    break

            except (IOError, OSError, PermissionError) as e:
                continue

        result["recent_exceptions"] = all_exceptions[:MAX_EXCEPTIONS]

    except Exception as e:
        result["check_error"] = str(e)

    return result


def calculate_heartbeat_age(timestamp_str: Optional[str]) -> Optional[float]:
    """Calculate age of heartbeat in seconds from ISO8601 timestamp string."""
    if not timestamp_str:
        return None

    try:
        # Try parsing ISO8601 format
        if 'T' in timestamp_str:
            # Handle various ISO8601 formats
            formats = [
                "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
            ]
            for fmt in formats:
                try:
                    dt = datetime.strptime(timestamp_str.replace('+00:00', 'Z').rstrip('Z') + 'Z', fmt.replace('%z', 'Z'))
                    # Make timezone aware
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return (datetime.now(timezone.utc) - dt).total_seconds()
                except ValueError:
                    continue

            # Manual parse for +00:00 format
            if '+' in timestamp_str or timestamp_str.endswith('Z'):
                clean_ts = timestamp_str.replace('Z', '+00:00')
                dt = datetime.fromisoformat(clean_ts)
                return (datetime.now(timezone.utc) - dt).total_seconds()

        # Try direct fromisoformat for Python 3.7+
        dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()

    except (ValueError, TypeError, AttributeError):
        return None


def build_verdict(
    process_check: Dict[str, Any],
    api_check: Dict[str, Any],
    database_check: Dict[str, Any],
    heartbeat_status: Dict[str, Any]
) -> str:
    """Determine overall health verdict based on all checks."""
    is_stale = heartbeat_status.get("is_stale", True)
    process_running = process_check.get("is_running", False)
    api_reachable = api_check.get("reachable", False)

    if not is_stale and process_running and api_reachable:
        return "healthy"
    elif is_stale:
        return "stale"
    else:
        return "unknown"


def generate_recommendations(
    verdict: str,
    process_check: Dict[str, Any],
    api_check: Dict[str, Any],
    database_check: Dict[str, Any],
    log_check: Dict[str, Any],
    heartbeat_status: Dict[str, Any]
) -> List[str]:
    """Generate actionable recommendations based on findings."""
    recommendations: List[str] = []

    # Process-related recommendations
    if not process_check.get("pid_exists", False):
        recommendations.append("Process not found - verify write_service is deployed and started")
        recommendations.append("Check systemd service status or container orchestration")

    if process_check.get("is_zombie", False):
        recommendations.append("Process is zombie state - restart write_service immediately")

    if process_check.get("error"):
        if "permission" in process_check["error"].lower():
            recommendations.append("Permission denied accessing process info - verify service user permissions")
        else:
            recommendations.append(f"Process check error: {process_check['error']}")

    # API-related recommendations
    if not api_check.get("reachable", False):
        recommendations.append("API endpoint not reachable - check write_service is listening on port 8772")
        recommendations.append("Verify firewall rules and network connectivity")

    if api_check.get("error"):
        if "timeout" in api_check["error"].lower():
            recommendations.append("API health check timed out - service may be hung or overloaded")
        elif "connection" in api_check["error"].lower():
            recommendations.append("Connection refused - ensure write_service HTTP server is running")

    # Database-related recommendations
    if database_check.get("query_error"):
        if "connection" in database_check["query_error"].lower():
            recommendations.append("Database connection error through API - verify PostgreSQL is accessible")
        else:
            recommendations.append(f"Database query error: {database_check['query_error']}")

    # Heartbeat staleness recommendations
    if heartbeat_status.get("is_stale", False):
        age = heartbeat_status.get("current_age_seconds", 0)
        recommendations.append(f"Heartbeat is stale ({int(age)}s > {HEARTBEAT_THRESHOLD}s threshold)")
        recommendations.append("Check if write_service process crashed or is blocked on I/O")
        recommendations.append("Review recent exceptions in logs to identify root cause")

    # Log-related recommendations
    if log_check.get("check_error"):
        recommendations.append(f"Log scan encountered error: {log_check['check_error']}")
    elif log_check.get("recent_exceptions"):
        exc_count = len(log_check["recent_exceptions"])
        recommendations.append(f"Found {exc_count} exception/error entries in logs - review for patterns")
        recommendations.append("Common causes: database connection loss, OOM kills, signal interruptions")

    # Resource-related recommendations
    memory_mb = process_check.get("memory_mb", 0)
    if memory_mb > 500:
        recommendations.append(f"High memory usage ({memory_mb:.0f}MB) - possible memory leak")

    # General recommendations based on verdict
    if verdict == "healthy":
        recommendations.append("Service appears healthy - continue monitoring")
    elif verdict == "stale":
        recommendations.append("Investigate service health immediately - consider graceful restart")
    elif verdict == "unknown":
        recommendations.append("Unable to determine service status - manual inspection required")
        recommendations.append("Check service logs and system journal for errors")

    # De-duplicate while preserving order
    seen = set()
    unique_recs = []
    for rec in recommendations:
        if rec not in seen:
            seen.add(rec)
            unique_recs.append(rec)

    return unique_recs


def run_diagnostics() -> Dict[str, Any]:
    """
    Run all diagnostic checks and return structured results.
    Read-only operations only - no writes, no fixes.
    """
    timestamp = get_timestamp()

    # Run all checks with timeout protection
    process_check = check_process_liveness()
    api_check = check_api_responsiveness()
    database_check = check_database_heartbeat()
    log_check = scan_logs_for_exceptions()

    # Determine heartbeat status
    last_ts = database_check.get("last_heartbeat_timestamp")
    heartbeat_age = calculate_heartbeat_age(last_ts)

    heartbeat_status = {
        "current_age_seconds": heartbeat_age if heartbeat_age is not None else -1,
        "threshold_seconds": HEARTBEAT_THRESHOLD,
        "is_stale": heartbeat_age is None or heartbeat_age >= HEARTBEAT_THRESHOLD
    }

    # Build verdict
    verdict = build_verdict(process_check, api_check, database_check, heartbeat_status)

    # Generate recommendations
    recommendations = generate_recommendations(
        verdict, process_check, api_check, database_check, log_check, heartbeat_status
    )

    # Assemble final result
    result = {
        "timestamp": timestamp,
        "heartbeat_status": heartbeat_status,
        "process_check": process_check,
        "api_check": api_check,
        "database_check": database_check,
        "log_check": log_check,
        "verdict": verdict,
        "recommendations": recommendations
    }

    return result


if __name__ == '__main__':
    result = run_diagnostics()
    print(json.dumps(result, indent=2))
    verdict = result.get('verdict', 'unknown')
    sys.exit(0 if verdict in ('healthy', 'stale', 'unknown') else 1)