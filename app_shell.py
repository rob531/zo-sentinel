#!/usr/bin/env python3
"""app_shell.py -- the SAME-ORIGIN SHELL (piece A).

ONE FastAPI app that puts a single front door in front of the zo-sentinel
constellation: it serves the product UI and proxies every API call to the
existing per-port services, server-side. The browser only ever sees one origin,
so the UIs stop depending on `http://127.0.0.1:PORT` and survive any surface --
localhost, the GH E2E runner, or a cloud host.

WHY A PROXY (not in-process mounts) FOR v1:
  Every service is its own `app = FastAPI()` with module-level side effects, and
  the launcher (not the source) assigns ports. Proxying needs ZERO changes to
  those services and runs ALONGSIDE them -- nothing in the live build loop is at
  risk. Convert a service to a true in-process mount later (Heroku single-dyno)
  by importing its `app` and `app.mount(prefix, that_app)` -- see MOUNTS below.

RUN (alongside the existing services):
    PORT=8080 python app_shell.py
    # then open http://127.0.0.1:8080/   (same origin for UI + API)

It reads $PORT (Heroku/Render/Fly convention) so this same file is the cloud web
process later. /healthz probes every upstream so a wrong port in app_routes.py
shows up immediately.

SECURITY NOTE: the UI today posts raw SQL to the bus (/bus/query|execute|write).
Proxying keeps that working but it is a hole on a security product -- a browser
issuing arbitrary SQL to the single writer. `BUS_ALLOW_WRITES` (default on, to
stay drop-in) gates /bus/execute and /bus/write; flip it OFF once those raw-SQL
calls are replaced by real endpoints. /bus/query (read) is always allowed.
"""
import os
import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse

import app_routes as R

UI_DIR = os.environ.get("UI_DIR", os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("PORT", "8080"))
UPSTREAM_HOST = os.environ.get("UPSTREAM_HOST", "127.0.0.1")
BUS_ALLOW_WRITES = os.environ.get("BUS_ALLOW_WRITES", "1") == "1"
# hop-by-hop headers must not be forwarded
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length"}

app = FastAPI(title="zo-sentinel same-origin shell", docs_url="/_shell/docs")
_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))  # read=None: allow SSE/streaming


# --- MOUNTS (opt-in true in-process collapse; off by default) -----------------
# Once a service module is verified import-clean, flip it here to run IN-process
# instead of proxied (required for a Heroku-style single dyno). Example:
#   from forensic_detail_api_v2 import app as forensic_app
#   app.mount("/servers", forensic_app)
# Leave empty for v1 -- everything is proxied, additive, zero-risk.


# --- UI: serve the product pages same-origin (fixes "orphaned UI") ------------
def _serve(filename: str):
    path = os.path.join(UI_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(404, f"UI file not found: {filename}")
    return FileResponse(path)

for _url, (_file, _admin) in R.UI_FILES.items():
    def _make(file=_file, admin=_admin):
        async def _route(request: Request):
            # admin pages: auth is enforced by the proxied API; an optional
            # same-origin guard can go here later (X-API-Key check).
            return _serve(file)
        return _route
    app.add_api_route(_url, _make(), methods=["GET"], include_in_schema=False)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    """Probe every configured upstream so a wrong port is obvious immediately."""
    seen, results = set(), {}
    for pref, port in R.ROUTES.items():
        if port in seen:
            continue
        seen.add(port)
        url = f"http://{UPSTREAM_HOST}:{port}/"
        try:
            r = await _client.request("GET", url, timeout=2.0)
            results[port] = {"prefix": pref, "reachable": True, "status": r.status_code}
        except Exception as e:
            results[port] = {"prefix": pref, "reachable": False, "error": type(e).__name__}
    ok = all(v["reachable"] for v in results.values())
    return JSONResponse({"shell": "ok", "all_upstreams_reachable": ok, "upstreams": results})


# --- the reverse proxy --------------------------------------------------------
async def _proxy(request: Request, prefix: str, port: int):
    # strip the prefix; forward the remainder + query string verbatim
    rest = request.url.path[len(prefix):] or "/"
    url = f"http://{UPSTREAM_HOST}:{port}{rest}"
    if request.url.query:
        url += "?" + request.url.query
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    try:
        req = _client.build_request(request.method, url, headers=headers, content=body)
        upstream = await _client.send(req, stream=True)
    except httpx.ConnectError:
        raise HTTPException(502, f"upstream {port} unreachable (check app_routes.py / go.sh)")
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP}
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=resp_headers,
        background=_closer(upstream),
    )


def _closer(upstream):
    from starlette.background import BackgroundTask
    return BackgroundTask(upstream.aclose)


@app.api_route("/{full_path:path}",
               methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
               include_in_schema=False)
async def gateway(request: Request, full_path: str):
    path = "/" + full_path
    match = R.prefix_for_path(path)
    if not match:
        raise HTTPException(404, f"no route for {path}")
    prefix, port = match
    # bus write-guard: block raw SQL writes from the browser unless explicitly allowed
    if prefix == "/bus" and not BUS_ALLOW_WRITES and path.rstrip("/").endswith(("/execute", "/write")):
        raise HTTPException(403, "bus writes disabled (BUS_ALLOW_WRITES=0); use a real endpoint")
    return await _proxy(request, prefix, port)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
