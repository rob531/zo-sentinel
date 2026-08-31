"""
Router for directive_queue_health_api service.
Exposes health metrics for directive queue processing.
"""
import os
import sys
import time
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
import requests

from app.db import get_session


# Default paths for directive directories
DEFAULT_PENDING_DIR = os.environ.get(
    "DIRECTIVE_PENDING_DIR",
    "/home/workspace/zo_sentinel/directives/pending"
)
DEFAULT_PROPOSED_DIR = os.environ.get(
    "DIRECTIVE_PROPOSED_DIR",
    "/home/workspace/zo_sentinel/directives/proposed"
)

# Threshold in seconds for marking directives as stale
STALE_THRESHOLD_SECONDS = 3600.0


class DirectiveQueueHealthResponse(BaseModel):
    """Response model for directive queue health check."""
    pending_count: int
    proposed_count: int
    oldest_pending_age_seconds: float
    stale_directives: list[str]


def check_directive_queue_health(
    pending_dir: str = DEFAULT_PENDING_DIR,
    proposed_dir: str = DEFAULT_PROPOSED_DIR,
    stale_threshold: float = STALE_THRESHOLD_SECONDS,
) -> DirectiveQueueHealthResponse:
    """
    Check health of directive queue by analyzing pending and proposed directories.
    
    Reads task names from directives/pending/ and directives/proposed/ directories,
    counts pending vs proposed directives, computes oldest_pending_age_seconds by
    parsing mtime on each JSON file, and returns stale_directives list of directive
    names older than stale_threshold.
    
    Args:
        pending_dir: Path to pending directives directory
        proposed_dir: Path to proposed directives directory
        stale_threshold: Age in seconds beyond which a directive is considered stale
    
    Returns:
        DirectiveQueueHealthResponse with counts and staleness info
    """
    pending_files = []
    proposed_files = []
    
    if os.path.isdir(pending_dir):
        pending_files = [
            f for f in os.listdir(pending_dir)
            if f.endswith('.json') and os.path.isfile(os.path.join(pending_dir, f))
        ]
    
    if os.path.isdir(proposed_dir):
        proposed_files = [
            f for f in os.listdir(proposed_dir)
            if f.endswith('.json') and os.path.isfile(os.path.join(proposed_dir, f))
        ]
    
    pending_count = len(pending_files)
    proposed_count = len(proposed_files)
    
    oldest_pending_age = 0.0
    stale_directives = []
    current_time = time.time()
    
    for filename in pending_files:
        filepath = os.path.join(pending_dir, filename)
        file_mtime = os.path.getmtime(filepath)
        age = current_time - file_mtime
        if age > oldest_pending_age:
            oldest_pending_age = age
        if age > stale_threshold:
            stale_directives.append(filename)
    
    return DirectiveQueueHealthResponse(
        pending_count=pending_count,
        proposed_count=proposed_count,
        oldest_pending_age_seconds=oldest_pending_age,
        stale_directives=stale_directives
    )


def create_router() -> APIRouter:
    """Create and configure the APIRouter for directive queue health."""
    router = APIRouter()
    
    @router.get(
        "/health",
        response_model=DirectiveQueueHealthResponse,
        summary="Get directive queue health metrics"
    )
    async def get_health() -> DirectiveQueueHealthResponse:
        """
        Get health metrics for the directive queue.
        
        Returns counts of pending and proposed directives, age of oldest
        pending directive, and list of stale directives.
        """
        health_data = check_directive_queue_health()
        
        # Read sentinel_directive_generator daemon health from write_service
        try:
            resp = requests.get(
                "http://127.0.0.1:8772/health",
                timeout=2
            )
            if resp.status_code == 200:
                health_data_dict = resp.json()
                health_data_dict["sentinel_directive_generator_age_seconds"] = health_data_dict.get("age_seconds", -1)
        except Exception:
            pass
        
        return health_data
    
    return router


def get_router() -> APIRouter:
    """Public interface to get the configured router."""
    return create_router()


if __name__ == "__main__":
    # Self-test: create temp JSON files in mock pending/proposed dirs,
    # call the health function, assert results
    
    pending_dir = tempfile.mkdtemp(prefix="directive_pending_")
    proposed_dir = tempfile.mkdtemp(prefix="directive_proposed_")
    
    # Store original paths to restore later
    original_pending_dir = DEFAULT_PENDING_DIR
    original_proposed_dir = DEFAULT_PROPOSED_DIR
    
    # Patch the defaults for testing
    os.environ["DIRECTIVE_PENDING_DIR"] = pending_dir
    os.environ["DIRECTIVE_PROPOSED_DIR"] = proposed_dir
    
    # Create test files
    pending_file1 = os.path.join(pending_dir, "test_dir1.json")
    pending_file2 = os.path.join(pending_dir, "test_dir2.json")
    proposed_file = os.path.join(proposed_dir, "test_dir3.json")
    
    Path(pending_file1).write_text('{"directive": "test_dir1"}')
    Path(pending_file2).write_text('{"directive": "test_dir2"}')
    Path(proposed_file).write_text('{"directive": "test_dir3"}')
    
    # Make pending files stale (older than 3600s)
    old_time = time.time() - 7200
    os.utime(pending_file1, (old_time, old_time))
    os.utime(pending_file2, (old_time, old_time))
    
    try:
        # Re-import to pick up new defaults
        from importlib import reload
        import router as router_module
        reload(router_module)
        
        result = router_module.check_directive_queue_health(
            pending_dir=pending_dir,
            proposed_dir=proposed_dir
        )
        
        # Assertions
        assert result.pending_count == 2, f"Expected pending_count==2, got {result.pending_count}"
        assert result.proposed_count == 1, f"Expected proposed_count==1, got {result.proposed_count}"
        assert "test_dir1.json" in result.stale_directives, f"test_dir1.json not in stale_directives: {result.stale_directives}"
        assert "test_dir2.json" in result.stale_directives, f"test_dir2.json not in stale_directives: {result.stale_directives}"
        
        # Restore environment
        os.environ["DIRECTIVE_PENDING_DIR"] = original_pending_dir
        os.environ["DIRECTIVE_PROPOSED_DIR"] = original_proposed_dir
        
        print("PASS")
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)
    finally:
        # Cleanup
        shutil.rmtree(pending_dir, ignore_errors=True)
        shutil.rmtree(proposed_dir, ignore_errors=True)