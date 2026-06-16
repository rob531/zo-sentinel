#!/usr/bin/env python3
"""it_write_service.py -- a REAL-DuckDB-backed write_service for INTEGRATION tests.

Unlike tests/mock_write_service.py (a pure in-memory Python dict with regex
WHERE-clause parsing, no SQL engine at all), this stands up an ACTUAL DuckDB and
serves the production write_service HTTP contract (/write, /query, /execute,
/health) over it. So integration tests exercise REAL SQL end to end:
    POST /write {table, rows}  ->  DuckDB  ->  POST /query "SELECT ..."  (round-trip)
which is the exact access pattern every daemon/API uses against write_service:8772.
A dict-mock cannot catch real DB-path bugs (e.g. the #174 build_artifact write
that returned ok but never landed); a real-DuckDB service can.

Schema is loaded from schemas/duckdb_schema.json so tables match production.

DB-BACKEND PLUGGABLE (for the planned DuckDB->Postgres app migration, see
docs/POSTGRES_APP_MIGRATION_SCOPE.md): set IT_DB_BACKEND=postgres + IT_DB_DSN to
run the SAME integration tests against a Postgres service container. DuckDB is the
default today; the Postgres branch is a stub until the migration begins.

Hermetic: ephemeral in-process DB, no ZoComputer host, no :8772 to the live box.
"""
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "schemas" / "duckdb_schema.json"
BACKEND = os.environ.get("IT_DB_BACKEND", "duckdb")
DB_PATH = os.environ.get("IT_DB_PATH", ":memory:")

app = FastAPI()
_con = None
_tables: set = set()
_tcols: dict = {}


def _connect():
    if BACKEND == "duckdb":
        import duckdb
        return duckdb.connect(DB_PATH)
    # Postgres branch is intentionally a stub until the migration begins; the
    # same /write,/query,/execute contract will run against a service container.
    raise SystemExit(
        f"IT_DB_BACKEND={BACKEND!r} not wired yet "
        "(postgres planned -- see docs/POSTGRES_APP_MIGRATION_SCOPE.md)"
    )


def _create_schema(con) -> set:
    spec = json.loads(SCHEMA.read_text(encoding="utf-8")).get("tables", {})
    for table, cols in spec.items():
        coldefs = ", ".join(f'"{c["name"]}" {c["type"]}' for c in cols)
        con.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({coldefs})')
    return set(spec.keys())


@app.on_event("startup")
def _startup():
    global _con, _tables, _tcols
    _con = _connect()
    _tables = _create_schema(_con)
    for t in _tables:
        cur = _con.execute(f'SELECT * FROM "{t}" LIMIT 0')
        _tcols[t] = [d[0] for d in cur.description]


def _next_id(table: str) -> int:
    try:
        return int(_con.execute(f'SELECT COALESCE(MAX(id),0)+1 FROM "{table}"').fetchone()[0])
    except Exception:
        return 1


@app.post("/write")
async def write(request: Request):
    body = await request.json()
    table = body.get("table")
    rows = body.get("rows") or []
    if table not in _tables:
        return JSONResponse({"ok": False, "error": f"unknown table {table}"}, status_code=400)
    cols = _tcols[table]
    n = 0
    try:
        for row in rows:
            r = dict(row)
            if "id" in cols and "id" not in r:
                r["id"] = _next_id(table)
            use = [c for c in cols if c in r]
            if not use:
                continue
            vals = [json.dumps(r[c]) if isinstance(r[c], (dict, list)) else r[c] for c in use]
            colsql = ", ".join(f'"{c}"' for c in use)
            ph = ", ".join("?" for _ in use)
            _con.execute(f'INSERT INTO "{table}" ({colsql}) VALUES ({ph})', vals)
            n += 1
    except Exception as e:
        return JSONResponse({"ok": False, "queued": n, "error": str(e)}, status_code=500)
    return {"ok": True, "queued": n, "wait": True}


@app.post("/query")
async def query(request: Request):
    body = await request.json()
    try:
        cur = _con.execute(body.get("sql", ""))
        cnames = [d[0] for d in cur.description] if cur.description else []
        rows = [dict(zip(cnames, row)) for row in cur.fetchall()]
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e), "rows": []}, status_code=500)
    return {"ok": True, "rows": rows}


@app.post("/execute")
async def execute(request: Request):
    body = await request.json()
    try:
        _con.execute(body.get("sql", ""))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return {"ok": True}


@app.get("/health")
async def health():
    return {"status": "ok", "backend": BACKEND, "tables": len(_tables)}


def run():
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("IT_WS_PORT", "8772")), log_level="warning")


if __name__ == "__main__":
    run()
