import os
import sys
import time
import json
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
from functools import partial

import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

SERVICE_NAME = "sft_adapter_loader"
SERVICE_PORT = 8793
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"

ADAPTER_REPO = "rob531/zomesh-sentinel-sft"
ADAPTER_SUBFOLDER = "student_v1"
BASE_MODEL = "Qwen/Qwen2.5-3B"
FALLBACK_MODEL_URL = "http://127.0.0.1:8773/generate"
ADAPTER_TIMEOUT_SECS = 10
HEARTBEAT_INTERVAL = 30

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(SERVICE_NAME)

app = FastAPI(title=SERVICE_NAME)

_model = None
_tokenizer = None
_adapter_ready = False
_fallback_available = True
_start_time = time.time()


class GenerateRequest(BaseModel):
    task_type: str
    prompt: str
    server_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class GenerateResponse(BaseModel):
    response: str
    source: str
    latency_ms: float
    adapter_loaded: bool


def check_single_instance():
    """Ensure only one instance runs."""
    pid = os.getpid()
    try:
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        if old_pid != pid:
            import os as os_module
            try:
                os_module.kill(old_pid, 0)
                logger.error(f"Another instance running with PID {old_pid}. Exiting.")
                sys.exit(1)
            except:
                logger.warning(f"Stale PID file, overwriting.")
    except FileNotFoundError:
        pass
    
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    logger.info(f"Started with PID {pid}")


def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except:
        pass


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully.")
    remove_pid_file()
    sys.exit(0)


def get_write_url():
    return WRITE_SERVICE_URL


def get_query_url():
    return QUERY_SERVICE_URL


def ws_write(table: str, rows: list) -> bool:
    """Write to write_service."""
    try:
        resp = requests.post(
            get_write_url(),
            json={"table": table, "rows": rows, "wait": True},
            timeout=10
        )
        return resp.status_code == 200 and resp.json().get("ok", False)
    except Exception as e:
        logger.error(f"write_service error: {e}")
        return False


def ws_query(sql: str) -> Optional[list]:
    """Query write_service."""
    try:
        resp = requests.post(
            get_query_url(),
            json={"sql": sql},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("rows", [])
        return None
    except Exception as e:
        logger.error(f"query_service error: {e}")
        return None


def send_heartbeat():
    """Send service health heartbeat."""
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.utcnow().isoformat()
        }])
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


def log_to_mesh_memory(event_type: str, latency_ms: float, success: bool, details: Dict[str, Any]):
    """Log adapter latency and events to mesh_memory via write_service."""
    try:
        ws_write("mesh_memory", [{
            "event_type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "service": SERVICE_NAME,
            "latency_ms": latency_ms,
            "success": success,
            "adapter_loaded": _adapter_ready,
            "details_json": json.dumps(details)
        }])
    except Exception as e:
        logger.warning(f"Failed to log to mesh_memory: {e}")


def load_adapter():
    """Load the LoRA adapter with Qwen2.5-3B base model."""
    global _model, _tokenizer, _adapter_ready
    
    try:
        logger.info(f"Loading adapter from {ADAPTER_REPO}/{ADAPTER_SUBFOLDER}...")
        
        from transformers import AutoModelForCausalLM, AutoTokenizer
        import torch
        from peft import PeftModel, PeftConfig
        
        peft_config = PeftConfig.from_pretrained(f"{ADAPTER_REPO}/{ADAPTER_SUBFOLDER}")
        base_model_name = peft_config.base_model_name_or_path or BASE_MODEL
        
        logger.info(f"Loading base model: {base_model_name}")
        base_model = AutoModelForCausalLM.from_pretrained(
            base_model_name,
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            device_map="auto" if torch.cuda.is_available() else None,
            trust_remote_code=True
        )
        
        logger.info("Merging LoRA adapter...")
        _model = PeftModel.from_pretrained(base_model, f"{ADAPTER_REPO}/{ADAPTER_SUBFOLDER}")
        _model.eval()
        
        logger.info("Loading tokenizer...")
        _tokenizer = AutoTokenizer.from_pretrained(base_model_name, trust_remote_code=True)
        if _tokenizer.pad_token is None:
            _tokenizer.pad_token = _tokenizer.eos_token
        
        _adapter_ready = True
        logger.info("Adapter loaded successfully!")
        
    except Exception as e:
        logger.error(f"Failed to load adapter: {e}")
        _adapter_ready = False


def generate_with_adapter(prompt: str, timeout: float = ADAPTER_TIMEOUT_SECS) -> Optional[Dict[str, Any]]:
    """Generate using the LoRA adapter with timeout."""
    global _model, _tokenizer
    
    if not _adapter_ready or _model is None or _tokenizer is None:
        return None
    
    try:
        inputs = _tokenizer(prompt, return_tensors="pt", truncation=True, max_length=2048)
        
        if torch.cuda.is_available():
            inputs = {k: v.cuda() for k, v in inputs.items()}
        
        start_time = time.time()
        
        with torch.no_grad():
            outputs = _model.generate(
                **inputs,
                max_new_tokens=512,
                temperature=0.3,
                top_p=0.9,
                do_sample=True,
                repetition_penalty=1.1
            )
        
        latency_ms = (time.time() - start_time) * 1000
        
        if latency_ms > ADAPTER_TIMEOUT_SECS * 1000:
            logger.warning(f"Adapter latency {latency_ms:.0f}ms exceeded threshold")
            return None
        
        response = _tokenizer.decode(outputs[0], skip_special_tokens=True)
        
        return {
            "response": response,
            "latency_ms": latency_ms,
            "success": True
        }
        
    except Exception as e:
        logger.error(f"Adapter inference error: {e}")
        return None


def generate_with_fallback(prompt: str, metadata: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    """Fallback to MiniMax inference router."""
    global _fallback_available
    
    try:
        payload = {
            "task_type": "sentinel_assess",
            "prompt": prompt,
            "metadata": metadata or {}
        }
        
        start_time = time.time()
        resp = requests.post(FALLBACK_MODEL_URL, json=payload, timeout=30)
        latency_ms = (time.time() - start_time) * 1000
        
        if resp.status_code == 200:
            data = resp.json()
            _fallback_available = True
            return {
                "response": data.get("response", ""),
                "latency_ms": latency_ms,
                "success": True,
                "source": "fallback"
            }
        else:
            logger.error(f"Fallback returned status {resp.status_code}")
            _fallback_available = False
            return None
            
    except Exception as e:
        logger.error(f"Fallback inference error: {e}")
        _fallback_available = False
        return None


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest):
    """Generate using adapter or fallback."""
    start_time = time.time()
    
    if request.task_type != "sentinel_assess":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported task_type: {request.task_type}. Only 'sentinel_assess' is supported."
        )
    
    details = {
        "task_type": request.task_type,
        "server_id": request.server_id,
        "prompt_length": len(request.prompt)
    }
    
    if _adapter_ready:
        logger.info(f"Routing to adapter for task_type={request.task_type}")
        result = generate_with_adapter(request.prompt)
        
        if result and result.get("success"):
            total_latency_ms = (time.time() - start_time) * 1000
            
            log_to_mesh_memory(
                "adapter_inference",
                total_latency_ms,
                True,
                {**details, "adapter_latency_ms": result["latency_ms"]}
            )
            
            return GenerateResponse(
                response=result["response"],
                source="adapter",
                latency_ms=total_latency_ms,
                adapter_loaded=True
            )
        else:
            logger.warning("Adapter inference failed, falling back to MiniMax")
    else:
        logger.warning("Adapter not ready, using fallback")
    
    result = generate_with_fallback(request.prompt, request.metadata)
    
    if result and result.get("success"):
        total_latency_ms = (time.time() - start_time) * 1000
        
        log_to_mesh_memory(
            "fallback_inference",
            total_latency_ms,
            True,
            {**details, "source": "fallback", "latency_ms": result["latency_ms"]}
        )
        
        return GenerateResponse(
            response=result["response"],
            source="fallback",
            latency_ms=total_latency_ms,
            adapter_loaded=_adapter_ready
        )
    
    raise HTTPException(
        status_code=503,
        detail="Both adapter and fallback are unavailable"
    )


@app.get("/health")
async def health():
    """Health check endpoint."""
    uptime_seconds = time.time() - _start_time
    
    return {
        "status": "ok" if _adapter_ready or _fallback_available else "degraded",
        "service": SERVICE_NAME,
        "adapter_loaded": _adapter_ready,
        "fallback_available": _fallback_available,
        "uptime_seconds": uptime_seconds
    }


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": SERVICE_NAME,
        "version": "1.0.0",
        "adapter_repo": ADAPTER_REPO,
        "adapter_subfolder": ADAPTER_SUBFOLDER,
        "base_model": BASE_MODEL,
        "adapter_ready": _adapter_ready
    }


def heartbeat_loop():
    """Background heartbeat loop."""
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            logger.warning(f"Heartbeat error: {e}")
        time.sleep(HEARTBEAT_INTERVAL)


def run():
    """Main entry point for daemon."""
    check_single_instance()
    
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    
    logger.info("Loading LoRA adapter...")
    load_adapter()
    
    import threading
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=SERVICE_PORT,
        log_level="info"
    )


if __name__ == "__main__":
    run()