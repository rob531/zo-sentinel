import os
import time
import logging
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

ZO_SENTINEL_PATH = Path("/home/workspace/zo_sentinel")
KNOWLEDGE_BASE_PATH = ZO_SENTINEL_PATH / "KNOWLEDGE_BASE.md"
GENERATION_FAILURES_PATH = ZO_SENTINEL_PATH / "GENERATION_FAILURES.md"
BUILD_STATE_PATH = ZO_SENTINEL_PATH / "BUILD_STATE.md"
QUERY_URL = "http://127.0.0.1:8772/query"

_cache: Optional[str] = None
_cache_time: float = 0
CACHE_TTL_SECONDS = 300

def ws_query(sql: str) -> list:
    """Execute a query via write_service query endpoint."""
    try:
        import requests
        response = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=5
        )
        if response.status_code == 200:
            result = response.json()
            return result.get("data", [])
        return []
    except Exception as e:
        logger.debug(f"Query failed: {e}")
        return []

def get_registry_count() -> int:
    """Get total MCP servers in registry."""
    try:
        result = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
        if result and len(result) > 0:
            return result[0].get("cnt", 0) or 0
    except Exception:
        pass
    return 0

def get_unscored_count() -> int:
    """Get servers with null trust_score."""
    try:
        result = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE trust_score IS NULL")
        if result and len(result) > 0:
            return result[0].get("cnt", 0) or 0
    except Exception:
        pass
    return 0

def get_high_risk_count() -> int:
    """Get servers with verdict in (MALICIOUS, SUSPICIOUS)."""
    try:
        result = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE verdict IN ('MALICIOUS', 'SUSPICIOUS')")
        if result and len(result) > 0:
            return result[0].get("cnt", 0) or 0
    except Exception:
        pass
    return 0

def list_built_files() -> list:
    """List Python files with sizes in workspace."""
    files = []
    try:
        for path in sorted(ZO_SENTINEL_PATH.rglob("*.py")):
            try:
                size = path.stat().st_size
                rel = path.relative_to(ZO_SENTINEL_PATH)
                files.append(f"  {rel} ({size} bytes)")
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"File listing failed: {e}")
    return files

def read_file_cached(path: Path, max_lines: int = 100) -> str:
    """Read file with fallback."""
    try:
        if path.exists():
            with open(path, "r") as f:
                lines = f.readlines()[:max_lines]
                return "".join(lines)
    except Exception as e:
        logger.debug(f"Read {path} failed: {e}")
    return ""

def get_knowledge_base() -> str:
    """Read KNOWLEDGE_BASE.md content."""
    return read_file_cached(KNOWLEDGE_BASE_PATH, max_lines=50)

def get_build_failures() -> str:
    """Get last 3 failures from GENERATION_FAILURES.md."""
    content = read_file_cached(GENERATION_FAILURES_PATH, max_lines=150)
    if not content:
        return "No failures recorded."
    lines = content.split("\n")
    failure_lines = []
    in_failure = False
    failure_count = 0
    for line in reversed(lines):
        if line.startswith("## FAILURE"):
            failure_count += 1
            if failure_count <= 3:
                in_failure = True
        if in_failure:
            failure_lines.insert(0, line)
        if failure_count > 3:
            break
    return "\n".join(failure_lines) if failure_lines else "No failures recorded."

def get_build_state() -> str:
    """Read BUILD_STATE.md content."""
    return read_file_cached(BUILD_STATE_PATH, max_lines=100)

def get_build_context() -> str:
    """Build and return context string for generation prompts."""
    global _cache, _cache_time
    
    now = time.time()
    if _cache is not None and (now - _cache_time) < CACHE_TTL_SECONDS:
        logger.debug("Returning cached context")
        return _cache
    
    logger.info("Building fresh context")
    
    registry_count = get_registry_count()
    unscored_count = get_unscored_count()
    high_risk_count = get_high_risk_count()
    
    built_files = list_built_files()
    files_section = "\n".join(built_files) if built_files else "  (no files found)"
    
    kb_content = get_knowledge_base()
    failures = get_build_failures()
    build_state = get_build_state()
    
    context = f"""
## ZO-SENTINEL Current State

### Registry Stats
- Total MCP servers registered: {registry_count}
- Unscored servers: {unscored_count}
- High-risk servers (MALICIOUS/SUSPICIOUS): {high_risk_count}

### Built Files (last scan)
{files_section}

### Knowledge Base (excerpt)
{kb_content}

### Recent Build Failures
{failures}

### Build State Summary
{build_state}
"""
    
    _cache = context
    _cache_time = now
    return context

def clear_cache():
    """Clear cached context."""
    global _cache, _cache_time
    _cache = None
    _cache_time = 0
    logger.info("Context cache cleared")

def main():
    """Print context to stdout for debugging."""
    print(get_build_context())

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()