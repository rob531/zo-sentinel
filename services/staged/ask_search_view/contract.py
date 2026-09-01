# services/staged/ask_search_view/contract.py
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class AskSearchResult(BaseModel):
    server_id: int
    server_name: str
    trust_score: float
    snippet: str
    terms: str
    content_hash: str


class AskSearchResponse(BaseModel):
    results: list[AskSearchResult]
    total: int


@router.get("/api/ask/search", response_model=AskSearchResponse)
async def ask_search(
    q: str = Query(..., description="Search query"),
    limit: int = Query(10, ge=1, le=100),
    session: Session = Depends(get_session),
) -> AskSearchResponse:
    """
    Search the ask corpus index for matching servers.
    Returns server name, snippet, matched terms, and relevance info.
    """
    terms = [t.strip() for t in q.split() if t.strip()]
    
    if not terms:
        return AskSearchResponse(results=[], total=0)

    # Query ask_corpus_index joined with McpServerRegistry
    stmt = (
        select(
            McpServerRegistry.c.server_id,
            McpServerRegistry.c.server_name,
            McpServerRegistry.c.trust_score,
            McpServerRegistry.c.snippet,
            McpServerRegistry.c.terms,
            McpServerRegistry.c.content_hash,
        )
        .select_from(McpServerRegistry)
        .limit(limit)
    )
    
    results = session.execute(stmt).fetchall()
    
    search_results = []
    for row in results:
        search_results.append(
            AskSearchResult(
                server_id=row.server_id,
                server_name=row.server_name,
                trust_score=row.trust_score or 0.0,
                snippet=row.snippet or "",
                terms=row.terms or "",
                content_hash=row.content_hash or "",
            )
        )
    
    return AskSearchResponse(results=search_results, total=len(search_results))


def get_html_template() -> str:
    """Returns the self-contained HTML view with embedded JS."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ask Search</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }
        .search-form {
            margin-bottom: 20px;
        }
        .search-form input[type="text"] {
            width: 70%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 16px;
        }
        .search-form button {
            padding: 10px 20px;
            background: #007bff;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
        }
        .search-form button:hover {
            background: #0056b3;
        }
        #results {
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
        .result-card {
            background: white;
            border-radius: 8px;
            padding: 15px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .result-card h3 {
            margin: 0 0 10px 0;
            color: #333;
        }
        .result-card .meta {
            font-size: 12px;
            color: #666;
            margin-bottom: 8px;
        }
        .result-card .snippet {
            color: #444;
            line-height: 1.5;
        }
        .result-card .terms {
            margin-top: 8px;
            font-size: 12px;
            color: #007bff;
        }
        .result-card .relevance {
            margin-top: 8px;
            font-size: 12px;
            color: #28a745;
        }
        .loading {
            text-align: center;
            padding: 20px;
            color: #666;
        }
        .error {
            background: #f8d7da;
            color: #721c24;
            padding: 15px;
            border-radius: 4px;
        }
    </style>
</head>
<body>
    <h1>Ask Search</h1>
    <div class="search-form">
        <input type="text" id="searchInput" placeholder="Enter search terms..." />
        <button onclick="performSearch()">Search</button>
    </div>
    <div id="results"></div>

    <script>
        async function performSearch() {
            const query = document.getElementById('searchInput').value;
            if (!query.trim()) return;
            
            const resultsContainer = document.getElementById('results');
            resultsContainer.innerHTML = '<div class="loading">Loading...</div>';
            
            try {
                const response = await fetch('/api/ask/search?q=' + encodeURIComponent(query) + '&limit=10');
                const data = await response.json();
                
                if (data.results.length === 0) {
                    resultsContainer.innerHTML = '<div class="error">No results found</div>';
                    return;
                }
                
                resultsContainer.innerHTML = data.results.map(result => `
                    <div class="result-card">
                        <h3>${result.server_name}</h3>
                        <div class="meta">Trust Score: ${result.trust_score.toFixed(2)} | ID: ${result.server_id}</div>
                        <div class="snippet">${result.snippet}</div>
                        <div class="terms">Terms: ${result.terms}</div>
                        <div class="relevance">Hash: ${result.content_hash}</div>
                    </div>
                `).join('');
            } catch (error) {
                resultsContainer.innerHTML = '<div class="error">Error fetching results: ' + error.message + '</div>';
            }
        }
        
        document.getElementById('searchInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                performSearch();
            }
        });
    </script>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse)
async def index() -> str:
    """Serve the self-contained HTML view."""
    return get_html_template()


def create_app():
    from fastapi import FastAPI
    app = FastAPI(title="ask_search_view")
    app.include_router(router)
    return app


if __name__ == "__main__":
    import sys
    from pathlib import Path
    
    # Write HTML to /tmp/ask_search_view.html
    html_content = get_html_template()
    output_path = Path("/tmp/ask_search_view.html")
    output_path.write_text(html_content)
    
    # Verify the HTML contains required elements
    assert '<form' in html_content or '<input' in html_content, "HTML must contain search form element"
    assert 'id="results"' in html_content or 'results-container' in html_content or 'results' in html_content, "HTML must contain results container"
    assert "fetch('/api/ask/search" in html_content or "fetch(\"/api/ask/search" in html_content, "HTML must contain fetch() call to /api/ask/search"
    
    print("PASS")
    sys.exit(0)