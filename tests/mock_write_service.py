import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import logging
from typing import Dict, Any, List, Optional
import json
import re
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

DATA_STORE: Dict[str, List[Dict[str, Any]]] = {}

def parse_where_clause(where_clause: str, row: Dict[str, Any]) -> bool:
    """Parse simple WHERE clause and check if row matches."""
    if not where_clause:
        return True
    
    where_clause = where_clause.strip()
    
    conditions = re.split(r'\s+AND\s+', where_clause, flags=re.IGNORECASE)
    
    for condition in conditions:
        condition = condition.strip()
        
        like_match = re.match(r"(\w+)\s+LIKE\s+['\"](.+)['\"]", condition, re.IGNORECASE)
        if like_match:
            field = like_match.group(1)
            pattern = like_match.group(2).replace('%', '.*').replace('_', '.')
            if field in row:
                if not re.search(pattern, str(row[field]), re.IGNORECASE):
                    return False
            else:
                return False
            continue
        
        comp_match = re.match(r"(\w+)\s*([<>=!]+)\s*['\"]?(.+)['\"]?", condition, re.IGNORECASE)
        if comp_match:
            field = comp_match.group(1)
            op = comp_match.group(2)
            value = comp_match.group(3).strip("'\"")
            
            if field not in row:
                return False
            
            row_value = str(row[field])
            
            if op == '=':
                if row_value != value:
                    return False
            elif op == '!=':
                if row_value == value:
                    return False
            elif op == '>':
                try:
                    if float(row_value) <= float(value):
                        return False
                except ValueError:
                    if row_value <= value:
                        return False
            elif op == '<':
                try:
                    if float(row_value) >= float(value):
                        return False
                except ValueError:
                    if row_value >= value:
                        return False
            elif op == '>=':
                try:
                    if float(row_value) < float(value):
                        return False
                except ValueError:
                    if row_value < value:
                        return False
            elif op == '<=':
                try:
                    if float(row_value) > float(value):
                        return False
                except ValueError:
                    if row_value > value:
                        return False
            continue
        
        return False
    
    return True

@app.post("/write")
async def write(request: Request):
    try:
        body = await request.json()
        table = body.get("table", "default")
        rows = body.get("rows")
        
        if rows is None:
            return JSONResponse(
                status_code=400,
                content={"error": "Missing 'rows' field"}
            )
        
        if not isinstance(rows, list):
            rows = [rows]
        
        if table not in DATA_STORE:
            DATA_STORE[table] = []
        
        timestamp = datetime.utcnow().isoformat()
        for row in rows:
            row_copy = dict(row)
            row_copy["_written_at"] = timestamp
            DATA_STORE[table].append(row_copy)
        
        logger.info(f"Wrote {len(rows)} rows to table '{table}'")
        
        return JSONResponse(content={
            "ok": True,
            "rows_written": len(rows),
            "table": table
        })
    except Exception as e:
        logger.error(f"Write error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

@app.post("/query")
async def query(request: Request):
    try:
        body = await request.json()
        sql = body.get("sql", "")
        
        match = re.match(r"SELECT\s+\*\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+))?", sql, re.IGNORECASE)
        if not match:
            return JSONResponse(
                status_code=400,
                content={"error": "Only simple SELECT * FROM table WHERE ... queries supported"}
            )
        
        table = match.group(1)
        where_clause = match.group(2) or ""
        
        if table not in DATA_STORE:
            return JSONResponse(content={"rows": []})
        
        results = []
        for row in DATA_STORE[table]:
            if parse_where_clause(where_clause, row):
                results.append(row)
        
        logger.info(f"Query on '{table}' returned {len(results)} rows")
        
        return JSONResponse(content={"rows": results})
    except Exception as e:
        logger.error(f"Query error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

@app.post("/execute")
async def execute(request: Request):
    try:
        body = await request.json()
        sql = body.get("sql", "")
        
        logger.info(f"Execute (no-op): {sql}")
        
        return JSONResponse(content={"ok": True})
    except Exception as e:
        logger.error(f"Execute error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

@app.get("/health")
async def health():
    return JSONResponse(content={
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "tables": len(DATA_STORE),
        "total_rows": sum(len(rows) for rows in DATA_STORE.values())
    })

@app.get("/dump")
async def dump():
    return JSONResponse(content={
        "tables": DATA_STORE,
        "timestamp": datetime.utcnow().isoformat()
    })

@app.post("/reset")
async def reset():
    global DATA_STORE
    DATA_STORE = {}
    logger.info("All data reset")
    return JSONResponse(content={"ok": True, "message": "All data cleared"})

def run():
    import sys
    port = 8799
    if len(sys.argv) > 1 and sys.argv[1] == "--port":
        port = int(sys.argv[2])
    elif "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])
    
    import uvicorn
    logger.info(f"Starting mock write service on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

if __name__ == "__main__":
    run()