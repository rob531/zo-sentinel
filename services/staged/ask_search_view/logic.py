# services/staged/ask_search_view/logic.py

from typing import List, Dict, Any, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db import get_session


def get_session_func() -> Session:
    """Get database session."""
    return get_session()


def search_ask_corpus(
    session: Session,
    q: str,
    limit: int = 20
) -> List[Dict[str, Any]]:
    """
    Search the ask_corpus_index table and join with McpServerRegistry
    to return server name, snippet, matched terms, and relevance score.
    
    Args:
        session: SQLAlchemy session
        q: Search query string (terms separated by spaces)
        limit: Maximum number of results to return
        
    Returns:
        List of dicts with server_name, snippet, terms, content_hash, trust_score, relevance
    """
    if not q or not q.strip():
        return []
    
    search_terms = q.strip().split()
    
    query = text("""
        SELECT 
            aci.server_id,
            aci.snippet,
            aci.terms,
            aci.content_hash,
            msr.name as server_name,
            msr.trust_score
        FROM ask_corpus_index aci
        LEFT JOIN McpServerRegistry msr ON aci.server_id = msr.id
        WHERE aci.snippet ILIKE :search_pattern
           OR aci.terms ILIKE :search_pattern
        ORDER BY msr.trust_score DESC NULLS LAST, aci.snippet
        LIMIT :limit
    """)
    
    like_pattern = f"%{'%'.join(search_terms)}%"
    
    result = session.execute(query, {
        "search_pattern": like_pattern,
        "limit": limit
    })
    
    rows = result.fetchall()
    
    results = []
    for row in rows:
        server_name = row.server_name or f"Server-{row.server_id}"
        trust_score = row.trust_score
        
        relevance = 0.5
        if trust_score is not None:
            relevance = min(1.0, max(0.0, trust_score / 100.0))
        
        snippet_lower = (row.snippet or "").lower()
        terms_lower = (row.terms or "").lower()
        
        matched_count = sum(
            1 for term in search_terms 
            if term.lower() in snippet_lower or term.lower() in terms_lower
        )
        term_relevance = matched_count / len(search_terms) if search_terms else 0
        
        final_relevance = (relevance * 0.4) + (term_relevance * 0.6)
        
        results.append({
            "server_id": row.server_id,
            "server_name": server_name,
            "snippet": row.snippet,
            "terms": row.terms,
            "content_hash": row.content_hash,
            "trust_score": trust_score,
            "matched_terms": [t for t in search_terms if t.lower() in snippet_lower or t.lower() in terms_lower],
            "relevance": round(final_relevance, 4)
        })
    
    results.sort(key=lambda x: x["relevance"], reverse=True)
    
    return results[:limit]


HTML_VIEW_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ask Search</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; padding: 20px; }
        .container { max-width: 900px; margin: 0 auto; }
        h1 { color: #333; margin-bottom: 20px; }
        .search-form { display: flex; gap: 10px; margin-bottom: 20px; }
        .search-form input[type="text"] { flex: 1; padding: 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 16px; }
        .search-form button { padding: 12px 24px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }
        .search-form button:hover { background: #0056b3; }
        #results-container { display: flex; flex-direction: column; gap: 15px; }
        .result-card { background: white; border-radius: 8px; padding: 16px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .result-card .server-name { font-size: 18px; font-weight: 600; color: #333; margin-bottom: 8px; }
        .result-card .snippet { color: #555; line-height: 1.5; margin-bottom: 10px; }
        .result-card .meta { display: flex; gap: 15px; font-size: 13px; color: #777; }
        .result-card .relevance { background: #e9ecef; padding: 2px 8px; border-radius: 3px; }
        .result-card .matched-terms { color: #28a745; }
        .result-card .trust-score { color: #ffc107; }
        .loading { text-align: center; padding: 20px; color: #666; }
        .error { background: #f8d7da; color: #721c24; padding: 15px; border-radius: 4px; margin-bottom: 20px; }
        .no-results { text-align: center; padding: 40px; color: #666; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Ask Search</h1>
        <form class="search-form" id="search-form">
            <input type="text" id="search-input" name="q" placeholder="Search..." autocomplete="off">
            <button type="submit">Search</button>
        </form>
        <div id="results-container"></div>
    </div>
    <script>
        const API_BASE = '/api/ask/search';
        
        document.getElementById('search-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            const q = document.getElementById('search-input').value.trim();
            if (!q) return;
            
            const container = document.getElementById('results-container');
            container.innerHTML = '<div class="loading">Loading...</div>';
            
            try {
                const url = API_BASE + '?q=' + encodeURIComponent(q) + '&limit=20';
                const response = await fetch(url);
                
                if (!response.ok) {
                    throw new Error('Search failed: ' + response.status);
                }
                
                const results = await response.json();
                renderResults(results);
            } catch (error) {
                container.innerHTML = '<div class="error">' + error.message + '</div>';
            }
        });
        
        function renderResults(results) {
            const container = document.getElementById('results-container');
            
            if (!results || results.length === 0) {
                container.innerHTML = '<div class="no-results">No results found</div>';
                return;
            }
            
            container.innerHTML = results.map(r => {
                const snippet = escapeHtml(r.snippet || '');
                const matchedTerms = (r.matched_terms || []).map(t => '<span class="matched-terms">' + escapeHtml(t) + '</span>').join(', ');
                const relevance = (r.relevance * 100).toFixed(1) + '%';
                const trustScore = r.trust_score !== null ? r.trust_score : 'N/A';
                
                return '<div class="result-card">' +
                    '<div class="server-name">' + escapeHtml(r.server_name || 'Unknown') + '</div>' +
                    '<div class="snippet">' + snippet + '</div>' +
                    '<div class="meta">' +
                        '<span>Relevance: <span class="relevance">' + relevance + '</span></span>' +
                        '<span>Trust Score: ' + trustScore + '</span>' +
                        (matchedTerms ? '<span>Matched: ' + matchedTerms + '</span>' : '') +
                    '</div>' +
                '</div>';
            }).join('');
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        // Handle initial query from URL params
        const urlParams = new URLSearchParams(window.location.search);
        const initialQ = urlParams.get('q');
        if (initialQ) {
            document.getElementById('search-input').value = initialQ;
            document.getElementById('search-form').dispatchEvent(new Event('submit'));
        }
    </script>
</body>
</html>
'''


def generate_html_view() -> str:
    """Generate the HTML view for the ask search interface."""
    return HTML_VIEW_TEMPLATE


if __name__ == "__main__":
    import os
    from pathlib import Path
    
    output_path = Path("/tmp/ask_search_view.html")
    html_content = generate_html_view()
    
    output_path.write_text(html_content)
    
    assert '<form' in html_content and 'search-form' in html_content, "Search form element not found"
    
    assert 'results-container' in html_content, "Results container not found"
    
    assert "fetch(" in html_content and '/api/ask/search' in html_content, "fetch() call to /api/ask/search not found"
    
    print(f"HTML written to {output_path}")
    print("PASS")