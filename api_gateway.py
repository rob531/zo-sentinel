#!/usr/bin/env python3
"""
api_gateway.py -- ZO-SENTINEL API Gateway
Single entry point that proxies to all ZO-SENTINEL APIs.
Port 8787.
"""
import os
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import requests
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

try:
    from rate_limiter import RateLimiter
except ImportError:
    RateLimiter = None

SERVICE_NAME = "api_gateway"
PORT = 8787
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
HEARTBEAT_INTERVAL = 30

LOGGER = logging.getLogger(__name__)

SERVICE_ROUTES: Dict[str, Dict[str, Any]] = {
    "registry_api": {"host": "127.0.0.1", "port": 8781},
    "search_api": {"host": "127.0.0.1", "port": 8782},
    "approval_workflow": {"host": "127.0.0.1", "port": 8780},
    "dashboard_api": {"host": "127.0.0.1", "port": 8783},
    "bulk_assess_api": {"host": "127.0.0.1", "port": 8784},
    "comparison_api": {"host": "127.0.0.1", "port": 8785},
}

EXCLUDED_HEADERS = {"host", "content-length", "connection", "keep-alive", "transfer-encoding"}

_gateway_rate_limiter: Optional[RateLimiter] = None
_heartbeat_thread: Optional[threading.Thread] = None
_stop_heartbeat = threading.Event()


def get_rate_limiter() -> Optional[RateLimiter]:
    global _gateway_rate_limiter
    if _gateway_rate_limiter is None and RateLimiter is not None:
        _gateway_rate_limiter = RateLimiter(requests_per_minute=120, requests_per_hour=1000)
    return _gateway_rate_limiter


def send_heartbeat() -> bool:
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "status": "running"
            }
        }
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
        return resp.status_code in (200, 201)
    except Exception as e:
        LOGGER.warning(f"Heartbeat failed: {e}")
        return False


def log_gateway_event(
    event_type: str,
    method: str,
    path: str,
    target_service: Optional[str] = None,
    status_code: Optional[int] = None,
    error: Optional[str] = None
) -> None:
    try:
        payload = {
            "table": "mesh_events",
            "rows": {
                "event_type": event_type,
                "source": "api_gateway",
                "method": method,
                "path": path,
                "target_service": target_service,
                "status_code": status_code,
                "error_message": error,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        }
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
    except Exception:
        pass


def forward_request(
    method: str,
    path: str,
    target_host: str,
    target_port: int,
    headers: Dict[str, str],
    body: Optional[bytes] = None
) -> Response:
    url = f"http://{target_host}:{target_port}{path}"
    filtered_headers = {k: v for k, v in headers.items() if k.lower() not in EXCLUDED_HEADERS}
    filtered_headers["X-Forwarded-By"] = "ZO-SENTINEL-GATEWAY"
    filtered_headers["X-Sentinel-Version"] = "1.0.0"

    try:
        response = requests.request(
            method=method,
            url=url,
            headers=filtered_headers,
            data=body,
            timeout=30,
            allow_redirects=False
        )
        resp_headers = dict(response.headers)
        resp_headers["X-Proxied-By"] = "api_gateway"
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=resp_headers
        )
    except requests.exceptions.ConnectionError:
        return JSONResponse(
            status_code=503,
            content={"error": "Downstream service unavailable", "service": f"{target_host}:{target_port}"}
        )
    except requests.exceptions.Timeout:
        return JSONResponse(
            status_code=504,
            content={"error": "Downstream service timeout", "service": f"{target_host}:{target_port}"}
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Proxy error: {str(e)}"}
        )


async def proxy_request(request: Request, service_name: str, sub_path: str) -> Response:
    if service_name not in SERVICE_ROUTES:
        return JSONResponse(
            status_code=404,
            content={"error": "Unknown service", "available_services": list(SERVICE_ROUTES.keys())}
        )

    target = SERVICE_ROUTES[service_name]
    method = request.method
    path = request.url.path

    client_ip = request.headers.get("X-Real-IP", request.client.host if request.client else "unknown")
    limiter = get_rate_limiter()

    if limiter and not limiter.check(client_ip):
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limit exceeded",
                "retry_after": 60
            },
            headers={"Retry-After": "60"}
        )

    body = await request.body()

    log_gateway_event(
        event_type="gateway_request",
        method=method,
        path=path,
        target_service=service_name
    )

    response = forward_request(
        method=method,
        path=f"/{sub_path}" if sub_path else "/",
        target_host=target["host"],
        target_port=target["port"],
        headers=dict(request.headers),
        body=body if body else None
    )

    log_gateway_event(
        event_type="gateway_response",
        method=method,
        path=path,
        target_service=service_name,
        status_code=response.status_code if hasattr(response, 'status_code') else None
    )

    return response


app = FastAPI(title="ZO-SENTINEL API Gateway", version="1.0.0")


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        limiter = get_rate_limiter()
        if limiter:
            client_ip = request.headers.get("X-Real-IP", request.client.host if request.client else "unknown")
            if not limiter.check(client_ip):
                return JSONResponse(
                    status_code=429,
                    content={"error": "Rate limit exceeded"},
                    headers={"Retry-After": "60"}
                )
        response = await call_next(request)
        response.headers["X-Sentinel-Version"] = "1.0.0"
        return response


app.add_middleware(RateLimitMiddleware)


@app.post("/assess/{sub_path:path}")
async def assess_proxy(request: Request, sub_path: str):
    return await proxy_request(request, "registry_api", sub_path)


@app.get("/assess/{sub_path:path}")
async def assess_proxy_get(request: Request, sub_path: str):
    return await proxy_request(request, "registry_api", sub_path)


@app.post("/search/{sub_path:path}")
async def search_proxy(request: Request, sub_path: str):
    return await proxy_request(request, "search_api", sub_path)


@app.get("/search/{sub_path:path}")
async def search_proxy_get(request: Request, sub_path: str):
    return await proxy_request(request, "search_api", sub_path)


@app.post("/submit/{sub_path:path}")
async def submit_proxy(request: Request, sub_path: str):
    return await proxy_request(request, "approval_workflow", sub_path)


@app.get("/submit/{sub_path:path}")
async def submit_proxy_get(request: Request, sub_path: str):
    return await proxy_request(request, "approval_workflow", sub_path)


@app.post("/dashboard/{sub_path:path}")
async def dashboard_proxy(request: Request, sub_path: str):
    return await proxy_request(request, "dashboard_api", sub_path)


@app.get("/dashboard/{sub_path:path}")
async def dashboard_proxy_get(request: Request, sub_path: str):
    return await proxy_request(request, "dashboard_api", sub_path)


@app.post("/bulk/{sub_path:path}")
async def bulk_proxy(request: Request, sub_path: str):
    return await proxy_request(request, "bulk_assess_api", sub_path)


@app.get("/bulk/{sub_path:path}")
async def bulk_proxy_get(request: Request, sub_path: str):
    return await proxy_request(request, "bulk_assess_api", sub_path)


@app.post("/compare/{sub_path:path}")
async def compare_proxy(request: Request, sub_path: str):
    return await proxy_request(request, "comparison_api", sub_path)


@app.get("/compare/{sub_path:path}")
async def compare_proxy_get(request: Request, sub_path: str):
    return await proxy_request(request, "comparison_api", sub_path)


@app.get("/gateway/health")
async def gateway_health():
    results = {}
    all_healthy = True

    for service_name, config in SERVICE_ROUTES.items():
        try:
            url = f"http://{config['host']}:{config['port']}/health"
            resp = requests.get(url, timeout=5)
            results[service_name] = {
                "status": "healthy" if resp.status_code == 200 else "unhealthy",
                "response_code": resp.status_code
            }
            if resp.status_code != 200:
                all_healthy = False
        except requests.exceptions.ConnectionError:
            results[service_name] = {"status": "unreachable", "error": "Connection failed"}
            all_healthy = False
        except requests.exceptions.Timeout:
            results[service_name] = {"status": "timeout", "error": "Request timeout"}
            all_healthy = False
        except Exception as e:
            results[service_name] = {"status": "error", "error": str(e)}
            all_healthy = False

    overall_status = "healthy" if all_healthy else "degraded"

    return JSONResponse(
        status_code=200 if all_healthy else 503,
        content={
            "status": overall_status,
            "service": SERVICE_NAME,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "routes": {
                "/assess/*": "registry_api:8781",
                "/search/*": "search_api:8782",
                "/submit/*": "approval_workflow:8780",
                "/dashboard/*": "dashboard_api:8783",
                "/bulk/*": "bulk_assess_api:8784",
                "/compare/*": "comparison_api:8785"
            },
            "downstream_services": results
        }
    )


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": SERVICE_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/routes")
async def list_routes():
    return {
        "routes": [
            {"prefix": "/assess", "target": "registry_api:8781", "description": "Server assessment and registry"},
            {"prefix": "/search", "target": "search_api:8782", "description": "Search and discovery"},
            {"prefix": "/submit", "target": "approval_workflow:8780", "description": "Submission and approval workflow"},
            {"prefix": "/dashboard", "target": "dashboard_api:8783", "description": "Dashboard and reporting"},
            {"prefix": "/bulk", "target": "bulk_assess_api:8784", "description": "Bulk assessment operations"},
            {"prefix": "/compare", "target": "comparison_api:8785", "description": "Server comparison"}
        ],
        "gateway_version": "1.0.0"
    }


def heartbeat_loop():
    while not _stop_heartbeat.is_set():
        send_heartbeat()
        _stop_heartbeat.wait(HEARTBEAT_INTERVAL)


def run():
    global _heartbeat_thread
    _heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    _heartbeat_thread.start()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()