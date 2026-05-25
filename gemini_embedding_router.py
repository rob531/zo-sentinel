#!/usr/bin/env python3
"""
gemini_embedding_router.py -- Gemini Embedding Service for sqlite-vec

Exposes a local HTTP endpoint that converts text to vector embeddings
using Google's gemini-embedding-2 model via RcGeminiAPIKey.

This is the unlock for Phase 2 semantic search in mesh_memory.
Activates once MESH_MEMORY_USE_EMBEDDINGS=true is set in .zo_env.

Endpoints:
  GET  /health
  POST /embed          -- single text -> vector
  POST /embed/batch    -- list of texts -> list of vectors

Run: nohup python3 /home/workspace/zo_sentinel/gemini_embedding_router.py \
         >> /home/workspace/logs/gemini_embedding_router.log 2>&1 &

Port: 8788
Model: gemini-embedding-2 (free, 100 RPM, 2048 output dimensions)
Rate: self-throttles to 80 RPM (20% safety margin)
"""
import os
import sys
import json
import time
import logging
import requests
from datetime import datetime, timezone
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import uvicorn

WRITE_SERVICE = "http://127.0.0.1:8772"
SERVICE_NAME  = "gemini_embedding_router"
PORT          = 8788
MODEL         = "gemini-embedding-2"
GEMINI_URL    = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:embedContent"
RPM_CAP       = 80   # 100 free tier - 20% safety margin
DIMENSIONS    = 2048

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [embed_router] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler("/home/workspace/logs/gemini_embedding_router.log", mode="a"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(SERVICE_NAME)

app = FastAPI(title="Gemini Embedding Router", version="1.0.0")


# ---------------------------------------------------------------------------
# Rate limiter -- sliding 60s window
# ---------------------------------------------------------------------------

_request_times: deque = deque()

def _rate_limited() -> bool:
    now    = time.monotonic()
    cutoff = now - 60.0
    while _request_times and _request_times[0] < cutoff:
        _request_times.popleft()
    return len(_request_times) >= RPM_CAP

def _record_request():
    _request_times.append(time.monotonic())


# ---------------------------------------------------------------------------
# Gemini API call
# ---------------------------------------------------------------------------

def _get_key() -> str:
    return os.environ.get("RcGeminiAPIKey") or os.environ.get("GEMINI_API_KEY", "")


def embed_text(text: str) -> list[float]:
    """Embed a single text string. Returns a list of floats (2048 dims)."""
    key = _get_key()
    if not key:
        raise ValueError("RcGeminiAPIKey not set in environment")
    if _rate_limited():
        raise ValueError(f"Rate limit: >{RPM_CAP} requests/min")
    _record_request()

    payload = {
        "model":   f"models/{MODEL}",
        "content": {"parts": [{"text": text[:8000]}]},  # Gemini 8K input limit
        "taskType": "SEMANTIC_SIMILARITY",
    }
    r = requests.post(
        GEMINI_URL,
        headers={"Content-Type": "application/json",
                  "X-Goog-Api-Key": key},
        json=payload,
        timeout=30
    )
    if r.status_code != 200:
        raise ValueError(f"Gemini API error {r.status_code}: {r.text[:200]}")
    values = r.json().get("embedding", {}).get("values", [])
    if not values:
        raise ValueError("Empty embedding returned")
    return values


def _heartbeat():
    try:
        key_status = "SET" if _get_key() else "MISSING"
        recent = len(_request_times)
        requests.post(f"{WRITE_SERVICE}/write", json={
            "table": "service_health",
            "rows": {
                "service":        SERVICE_NAME,
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "meta": json.dumps({
                    "port": PORT, "model": MODEL,
                    "rpm_used": recent, "rpm_cap": RPM_CAP,
                    "key": key_status
                })
            }, "wait": True
        }, timeout=5)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class EmbedRequest(BaseModel):
    text: str
    task_type: Optional[str] = "SEMANTIC_SIMILARITY"

class EmbedResponse(BaseModel):
    embedding: list[float]
    dimensions: int
    model: str
    latency_ms: int

class BatchEmbedRequest(BaseModel):
    texts: list[str]

class BatchEmbedResponse(BaseModel):
    embeddings: list[list[float]]
    count: int
    dimensions: int
    model: str
    latency_ms: int
    errors: list[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    key_ok = bool(_get_key())
    recent = len(_request_times)
    return {
        "status":     "ok" if key_ok else "no_key",
        "model":      MODEL,
        "port":       PORT,
        "dimensions": DIMENSIONS,
        "rpm_used":   recent,
        "rpm_cap":    RPM_CAP,
        "key_set":    key_ok,
    }


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    t0 = time.monotonic()
    try:
        vector = embed_text(req.text)
    except ValueError as e:
        raise HTTPException(status_code=429 if "Rate limit" in str(e) else 503,
                            detail=str(e))
    latency = int((time.monotonic() - t0) * 1000)
    log.info("embed: %d chars -> %d dims in %dms", len(req.text), len(vector), latency)
    return EmbedResponse(embedding=vector, dimensions=len(vector),
                         model=MODEL, latency_ms=latency)


@app.post("/embed/batch", response_model=BatchEmbedResponse)
def embed_batch(req: BatchEmbedRequest):
    if not req.texts:
        raise HTTPException(status_code=400, detail="texts must not be empty")
    if len(req.texts) > 50:
        raise HTTPException(status_code=400,
                            detail="Max 50 texts per batch (rate limit protection)")
    t0       = time.monotonic()
    vectors  = []
    errors   = []
    for i, text in enumerate(req.texts):
        try:
            if _rate_limited():
                # Back off until window clears
                time.sleep(1)
            vectors.append(embed_text(text))
        except Exception as e:
            log.warning("batch[%d] failed: %s", i, e)
            errors.append(f"[{i}]: {e}")
            vectors.append([])  # placeholder so indices stay aligned
    latency = int((time.monotonic() - t0) * 1000)
    log.info("embed_batch: %d/%d ok in %dms", len(vectors)-len(errors), len(req.texts), latency)
    return BatchEmbedResponse(
        embeddings=vectors,
        count=len(vectors),
        dimensions=DIMENSIONS,
        model=MODEL,
        latency_ms=latency,
        errors=errors
    )


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
def startup():
    log.info("=" * 60)
    log.info("Gemini Embedding Router v1.0.0")
    log.info("  Model:     %s", MODEL)
    log.info("  Port:      %d", PORT)
    log.info("  Dims:      %d", DIMENSIONS)
    log.info("  RPM cap:   %d (free tier 100 - 20%% margin)", RPM_CAP)
    log.info("  Key:       %s", "SET" if _get_key() else "MISSING -- set RcGeminiAPIKey")
    log.info("  sqlite-vec: set MESH_MEMORY_USE_EMBEDDINGS=true to activate")
    log.info("=" * 60)
    _heartbeat()


def run():
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")


if __name__ == "__main__":
    run()