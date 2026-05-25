from fastapi import FastAPI
import uvicorn
import requests
import time
from datetime import datetime

SERVICE_NAME = "threat_feed_api"
PORT = 8789
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"
WRITE_URL = "http://127.0.0.1:8772/write"
HEARTBEAT_INTERVAL = 30

app = FastAPI()

start_time = time.time()

def ws_query(sql):
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Query error: {e}")
        return {"rows": [], "count": 0}

def ws_write(table, rows, wait=True):
    try:
        resp = requests.post(WRITE_URL, json={"table": table, "rows": rows, "wait": wait}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Write error: {e}")
        return {"ok": False}

def send_heartbeat():
    try:
        ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": datetime.utcnow().isoformat()})
    except Exception as e:
        print(f"Heartbeat error: {e}")

@app.get("/health")
def health():
    uptime = int(time.time() - start_time)
    return {"status": "ok", "service": SERVICE_NAME, "uptime": uptime}

@app.get("/threats")
def get_threats():
    sql = "SELECT server_id, threat_type, severity, evidence, reported_at FROM mcp_threat_associations ORDER BY reported_at DESC LIMIT 20"
    result = ws_query(sql)
    return {
        "threats": result.get("rows", []),
        "count": result.get("count", 0)
    }

def run():
    send_heartbeat()
    last_heartbeat = time.time()
    uvicorn.run(app, host="127.0.0.1", port=PORT)

if __name__ == "__main__":
    run()