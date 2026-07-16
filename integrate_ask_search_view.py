from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Any
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users
from ask_retrieval_service import search_ask_retrieval
import requests

app = FastAPI()
templates = Jinja2Templates(directory="templates")

def handle_search_request(query: str) -> Dict[str, Any]:
    try:
        results = search_ask_retrieval(query)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def display_results(results: Dict[str, Any]) -> None:
    if not results:
        raise HTTPException(status_code=404, detail="No results found")
    return results

def handle_error(error: str) -> None:
    raise HTTPException(status_code=500, detail=error)

@app.get("/ask_search", response_class=HTMLResponse)
async def ask_search_view(request: Request):
    return templates.TemplateResponse("ask_search_view.html", {"request": request})

@app.post("/ask_search")
async def ask_search(request: Request):
    form_data = await request.form()
    query = form_data.get("query")
    if not query:
        return templates.TemplateResponse("ask_search_view.html", {"request": request, "error": "Query is required"})

    try:
        results = handle_search_request(query)
        processed_results = display_results(results)
        return templates.TemplateResponse("ask_search_view.html", {"request": request, "results": processed_results})
    except HTTPException as e:
        handle_error(str(e))
        return templates.TemplateResponse("ask_search_view.html", {"request": request, "error": str(e)})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

    # Smoke test
    test_query = "test query"
    try:
        test_results = handle_search_request(test_query)
        display_results(test_results)
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")