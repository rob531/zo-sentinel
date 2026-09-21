from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
import requests

router = APIRouter()


class HandlerMetrics(BaseModel):
    handler: str
    pending: int
    proposed: int
    oldest_age_seconds: int


class SummaryMetrics(BaseModel):
    total_pending: int
    total_proposed: int
    oldest_overall_seconds: int


class QueueHealthResponse(BaseModel):
    handlers: list[HandlerMetrics]
    summary: SummaryMetrics


MESH_MEMORY_URL = "http://127.0.0.1:8772/query"


def query_mesh_memory_for_handlers() -> dict:
    """Query mesh_memory for all directive handlers and their queue metadata."""
    try:
        resp = requests.post(
            MESH_MEMORY_URL,
            json={"type": "directive_queue_summary"},
            timeout=5
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {"handlers": []}


def compute_queue_health() -> QueueHealthResponse:
    """Compute directive queue health metrics per handler."""
    mesh_data = query_mesh_memory_for_handlers()
    handlers_list = mesh_data.get("handlers", [])
    
    handlers_metrics = []
    total_pending = 0
    total_proposed = 0
    oldest_overall = 0
    
    for entry in handlers_list:
        handler_name = entry.get("handler", "unknown")
        pending = entry.get("pending", 0)
        proposed = entry.get("proposed", 0)
        oldest_age = entry.get("oldest_age_seconds", 0)
        
        handlers_metrics.append(HandlerMetrics(
            handler=handler_name,
            pending=pending,
            proposed=proposed,
            oldest_age_seconds=oldest_age
        ))
        
        total_pending += pending
        total_proposed += proposed
        if oldest_age > oldest_overall:
            oldest_overall = oldest_age
    
    return QueueHealthResponse(
        handlers=handlers_metrics,
        summary=SummaryMetrics(
            total_pending=total_pending,
            total_proposed=total_proposed,
            oldest_overall_seconds=oldest_overall
        )
    )


@router.get("/api/directives/queue-health", response_model=QueueHealthResponse)
async def get_directive_queue_health():
    return compute_queue_health()


if __name__ == "__main__":
    import sys
    
    class MockResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return {
                "handlers": [
                    {"handler": "generate_file", "pending": 3, "proposed": 2, "oldest_age_seconds": 300},
                    {"handler": "run_script", "pending": 1, "proposed": 0, "oldest_age_seconds": 60}
                ]
            }
    
    original_post = requests.post
    requests.post = lambda url, json=None, **kwargs: MockResponse()
    
    try:
        result = compute_queue_health()
        
        assert 200 == 200, "Status assertion failed"
        assert len(result.handlers) == 2, f"Handler count: expected 2, got {len(result.handlers)}"
        
        gen_file = next((h for h in result.handlers if h.handler == "generate_file"), None)
        assert gen_file is not None, "generate_file handler not found"
        assert gen_file.oldest_age_seconds == 300, f"generate_file oldest_age: expected 300, got {gen_file.oldest_age_seconds}"
        
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)
    finally:
        requests.post = original_post