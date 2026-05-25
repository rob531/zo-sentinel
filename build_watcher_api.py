#!/usr/bin/env python3
"""
build_watcher_api.py -- ZO-SENTINEL public build feed.
Port 8795. Serves dashboard.html at GET / and JSON feed at GET /api/build-stream.

v1.1: Fixed parse_manifest to show most-recent N builds regardless of age.
      Old WINDOW_MIN=90 logic caused empty feed whenever builder was idle >90min,
      falling through to DEMO placeholder data in the dashboard JS.
"""
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
import uvicorn, re, time, logging
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

MANIFEST     = Path('/home/workspace/zo_sentinel/BUILD_MANIFEST.md')
DASHBOARD    = Path('/home/workspace/zo_sentinel/dashboard.html')
PORT         = 8795
MAX_FILES    = 40   # show the 40 most recent successful builds
SERVICE_NAME = 'build_watcher_api'

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger(SERVICE_NAME)

class RateLimiter:
    def __init__(self, per_ip=20, global_limit=200, window=60):
        self.per_ip=per_ip; self.global_limit=global_limit; self.window=window
        self._ip=defaultdict(list); self._global=[]
    def is_allowed(self, ip):
        now=time.monotonic(); cut=now-self.window
        self._ip[ip]=[t for t in self._ip[ip] if t>cut]
        self._global=[t for t in self._global if t>cut]
        if len(self._global)>=self.global_limit or len(self._ip[ip])>=self.per_ip:
            return False
        self._ip[ip].append(now); self._global.append(now); return True

limiter = RateLimiter()

_SAFE = re.compile(r'^[\w\-\.]+$')
ROW_RE = re.compile(
    r'\|\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+UTC\s*'
    r'\|\s*Phase\s+[^|]+?\s*\|\s*`[^`]+`\s*\|\s*`([^`]+)`\s*\|\s*OK\b'
)

def parse_manifest():
    """Return the MAX_FILES most recent successful builds, regardless of age."""
    if not MANIFEST.exists():
        return []
    now = datetime.now(timezone.utc)
    entries = []  # (dt, name, age_display)
    seen = set()

    for line in MANIFEST.read_text(errors='replace').splitlines():
        m = ROW_RE.search(line)
        if not m:
            continue
        try:
            dt = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        name = Path(m.group(2).strip()).name
        if not name or not _SAFE.match(name) or len(name) > 80:
            continue
        if name in seen:
            continue
        seen.add(name)
        age_secs = int((now - dt).total_seconds())
        if age_secs < 60:
            age_display = 'just now'
        elif age_secs < 3600:
            age_display = f'{age_secs // 60}m ago'
        elif age_secs < 86400:
            age_display = f'{age_secs // 3600}h ago'
        else:
            age_display = f'{age_secs // 86400}d ago'
        entries.append((dt, name, age_display))

    # Sort newest-first, return top MAX_FILES
    entries.sort(key=lambda x: x[0], reverse=True)
    return [{'name': e[1], 'age_display': e[2]} for e in entries[:MAX_FILES]]

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['GET'],
                   allow_headers=['Content-Type'], max_age=86400)

@app.middleware('http')
async def rate_limit(request: Request, call_next):
    ip = request.client.host if request.client else 'unknown'
    if not limiter.is_allowed(ip):
        return Response(content='{"error":"rate limit"}', status_code=429,
                        media_type='application/json')
    return await call_next(request)

@app.get('/')
def dashboard():
    return FileResponse(str(DASHBOARD), media_type='text/html')

@app.get('/health')
def health():
    return JSONResponse({'status': 'ok', 'files_in_manifest': len(parse_manifest())})

@app.get('/api/build-stream')
def feed():
    files = parse_manifest()
    # Always 'active' if we have any builds at all — idle only if manifest is empty
    return JSONResponse({
        'status': 'active' if files else 'idle',
        'files': files,
        'count': len(files)
    })

def run():
    import threading, requests as _req
    def _beat():
        while True:
            try:
                _req.post('http://127.0.0.1:8772/write',
                    json={'table':'service_health',
                          'rows':{'service':SERVICE_NAME,
                                  'last_heartbeat':datetime.now(timezone.utc).isoformat()},
                          'wait':True},timeout=3)
            except Exception: pass
            time.sleep(300)
    threading.Thread(target=_beat, daemon=True).start()
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='warning', access_log=False)

if __name__ == '__main__':
    run()