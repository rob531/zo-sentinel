import logging
import subprocess
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Service constants
SERVICE_NAME = "diagnose_smoke_import_failures"
SERVICE_PORT = None  # diagnostic only, no port
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"

PROJECT_ROOT = Path("/home/workspace/zo_sentinel")
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Files to diagnose
DIAGNOSTIC_TARGETS = [
    "src/registry_api.py",
    "src/rug_pull_monitor.py",
    "src/signal_analyser.py",
]


def setup_logging() -> logging.Logger:
    log_path = LOG_DIR / f"{SERVICE_NAME}.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(str(log_path)),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger(SERVICE_NAME)


def send_heartbeat(service: str, logger: logging.Logger):
    payload = {
        "table": "service_health",
        "rows": {
            "service": service,
            "last_heartbeat": datetime.utcnow().isoformat(),
        },
        "wait": True,
    }
    try:
        import requests
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
        resp.raise_for_status()
        logger.debug(f"Heartbeat sent: {service}")
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


def ws_write(table: str, rows: dict, logger: logging.Logger):
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        import requests
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        resp.raise_for_status()
        logger.debug(f"Wrote to {table}: {rows}")
    except Exception as e:
        logger.warning(f"Write failed for {table}: {e}")


def read_smoke_output(logger: logging.Logger) -> list[dict]:
    smoke_dir = PROJECT_ROOT / "tests" / "smoke_output"
    entries = []
    if not smoke_dir.exists():
        logger.warning(f"Smoke output dir not found: {smoke_dir}")
        return entries
    for f in sorted(smoke_dir.glob("smoke_*.log")):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if datetime.now() - mtime > timedelta(hours=48):
                continue
            content = f.read_text()
            entries.append({"file": f.name, "mtime": mtime.isoformat(), "content": content})
        except Exception as e:
            logger.error(f"Failed to read {f}: {e}")
    return entries


def parse_failure_patterns(entries: list[dict], logger: logging.Logger) -> dict:
    import re
    patterns = {"import_errors": [], "file_not_found": [], "module_errors": [], "summary": {}}
    error_re = re.compile(r"File \"<string>\", line \d+")
    mod_re = re.compile(r"ModuleNotFoundError|No module named ['\"]([^'\"]+)['\"]")
    path_re = re.compile(r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]")
    
    for entry in entries:
        content = entry.get("content", "")
        if not content.strip():
            continue
        # Check for the specific pattern
        if 'File "<string>", line 10' in content or 'File "<frozen importlib' in content:
            # Extract error lines
            error_lines = [l for l in content.split("\n") if "Error" in l or "error" in l or "Exception" in l]
            match = mod_re.search(content)
            missing_module = match.group(1) if match else "unknown"
            patterns["import_errors"].append({
                "file": entry["file"],
                "mtime": entry["mtime"],
                "missing_module": missing_module,
                "error_snippet": " | ".join(error_lines[:5]),
            })
        # Check for file not found
        fnf = re.findall(r"FileNotFoundError.*?['\"]([^'\"]+)['\"]", content)
        for path in fnf:
            patterns["file_not_found"].append({
                "file": entry["file"],
                "missing_path": path,
            })
    
    patterns["summary"] = {
        "total_entries": len(entries),
        "import_errors": len(patterns["import_errors"]),
        "file_not_found": len(patterns["file_not_found"]),
    }
    return patterns


def check_python_path(logger: logging.Logger) -> dict:
    result = {"python_path": [], "site_packages": [], "project_structure": {}}
    import sys
    result["python_path"] = sys.path[:5]
    
    import site
    result["site_packages"] = site.getsitepackages()[:3]
    
    # Check project structure
    src_dir = PROJECT_ROOT / "src"
    if src_dir.exists():
        result["project_structure"]["src_files"] = [f.name for f in src_dir.glob("*.py")]
    return result


def check_importability(logger: logging.Logger) -> dict:
    results = {}
    for target in DIAGNOSTIC_TARGETS:
        fpath = PROJECT_ROOT / target
        module_name = target.replace("src/", "").replace(".py", "")
        results[target] = {"path": str(fpath), "exists": fpath.exists()}
        
        if not fpath.exists():
            logger.warning(f"Target not found: {fpath}")
            results[target]["import_test"] = "FILE_NOT_FOUND"
            continue
        
        # Attempt import with verbose
        try:
            import subprocess
            proc = subprocess.run(
                [sys.executable, "-c", f"import sys; sys.path.insert(0, '{PROJECT_ROOT}/src'); import {module_name}"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(PROJECT_ROOT),
            )
            results[target]["import_success"] = proc.returncode == 0
            results[target]["stderr"] = proc.stderr[:500] if proc.stderr else ""
            if proc.returncode != 0:
                logger.error(f"Import failed for {target}: {proc.stderr[:200]}")
        except Exception as e:
            results[target]["import_test"] = str(e)
            logger.error(f"Import test error for {target}: {e}")
    
    return results


def check_dependencies(logger: logging.Logger) -> dict:
    deps = {}
    critical = ["fastapi", "uvicorn", "requests", "duckdb", "httpx"]
    for pkg in critical:
        try:
            __import__(pkg.replace("-", "_"))
            deps[pkg] = "OK"
        except ImportError:
            deps[pkg] = "MISSING"
            logger.error(f"Missing dependency: {pkg}")
    return deps


def run() -> None:
    logger = setup_logging()
    logger.info(f"{SERVICE_NAME} starting diagnostic investigation")
    
    # Send initial heartbeat
    send_heartbeat(SERVICE_NAME, logger)
    
    # 1. Read smoke test output
    logger.info("Reading smoke test outputs...")
    smoke_entries = read_smoke_output(logger)
    logger.info(f"Found {len(smoke_entries)} recent smoke entries")
    
    # 2. Parse failure patterns
    logger.info("Parsing failure patterns...")
    patterns = parse_failure_patterns(smoke_entries, logger)
    logger.info(f"Import errors: {patterns['summary']['import_errors']}")
    
    # 3. Check Python path
    logger.info("Checking Python path configuration...")
    path_info = check_python_path(logger)
    
    # 4. Check dependencies
    logger.info("Checking dependency availability...")
    deps = check_dependencies(logger)
    
    # 5. Test importability
    logger.info("Testing importability of target files...")
    import_tests = check_importability(logger)
    
    # Compile diagnostic report
    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "service": SERVICE_NAME,
        "smoke_summary": patterns["summary"],
        "import_errors_detail": patterns["import_errors"][:10],
        "file_not_found_detail": patterns["file_not_found"][:10],
        "python_path": path_info["python_path"],
        "site_packages": path_info["site_packages"],
        "dependencies": deps,
        "import_tests": import_tests,
    }
    
    # Write diagnostic report
    report_path = LOG_DIR / f"{SERVICE_NAME}_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info(f"Diagnostic report written to {report_path}")
    
    # Write to write_service
    ws_write("smoke_diagnostic", report, logger)
    
    # Send heartbeat
    send_heartbeat(SERVICE_NAME, logger)
    
    logger.info(f"{SERVICE_NAME} diagnostic complete")
    
    # Print summary
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    import sys
    run()