"""Wire score_results_push_consumer into FastAPI lifespan."""
import threading
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.db import get_session
from app.models import MCPllmaxisscore, MCPServerregistry


def _create_consumer():
    """Create consumer instance lazily to avoid import-time side effects."""
    from score_results_push_consumer import ScoreResultsPushConsumer
    return ScoreResultsPushConsumer(
        db_session_factory=lambda: next(get_session()),
        write_service_base_url="http://127.0.0.1:8772"
    )


def _start_consumer_bg(consumer):
    """Start consumer.run() in background thread."""
    consumer.start()


@asynccontextmanager
async def _consumer_lifespan(app: FastAPI):
    """Lifespan contextmanager: start consumer on boot, stop on shutdown."""
    if getattr(app.state, 'score_results_push_running', False):
        yield
        return
    
    consumer = _create_consumer()
    app.state.score_results_push_running = True
    
    thread = threading.Thread(target=_start_consumer_bg, args=(consumer,), daemon=True)
    thread.start()
    app.state.score_results_push_thread = thread
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                "http://127.0.0.1:8772/service_health",
                json={"service": "score_results_push_consumer", "status": "up"}
            )
    except Exception:
        pass
    
    yield
    
    consumer.stop()
    app.state.score_results_push_running = False
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                "http://127.0.0.1:8772/service_health",
                json={"service": "score_results_push_consumer", "status": "down"}
            )
    except Exception:
        pass


app = FastAPI(lifespan=_consumer_lifespan)


@app.get("/health")
async def health():
    return {
        "score_results_push_consumer": getattr(
            app.state, 'score_results_push_running', False
        )
    }


@app.post("/score_results_push/process_pending")
async def process_pending():
    consumer = _create_consumer()
    result = consumer.process_pending_scores()
    return result


if __name__ == "__main__":
    from app.main import app as main_app
    print("PASS: main imports cleanly")