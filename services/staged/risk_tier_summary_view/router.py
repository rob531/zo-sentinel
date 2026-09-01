from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from typing import List, Dict
from collections import defaultdict

router = APIRouter()

def get_risk_tier_counts(session: Session) -> Dict[str, int]:
    """Count servers per risk_tier from McpServerRegistry."""
    counts = defaultdict(int)
    for server in session.query(McpServerRegistry.risk_tier).all():
        counts[server.risk_tier] += 1
    return dict(counts)

@router.get("/ui/risk/summary", response_class=HTMLResponse)
async def risk_tier_summary_view(request: Request, session: Session = Depends(get_session)):
    """Render HTML dashboard showing server counts per risk_tier."""
    counts = get_risk_tier_counts(session)
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Risk Tier Summary</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            h1 {{ color: #333; }}
            .chart {{ width: 600px; height: 400px; margin: 20px auto; }}
            .bar {{ display: inline-block; width: 20px; margin: 0 5px; text-align: center; }}
            .label {{ margin-top: 10px; text-align: center; }}
        </style>
    </head>
    <body>
        <h1>Risk Tier Summary</h1>
        <div class="chart">
            {''.join(f'<div class="bar" style="height: {count*20}px; background-color: {"#ff0000" if tier == "high" else "#ff9900" if tier == "medium" else "#00ff00"};"><div class="label">{count}</div></div>' for tier, count in counts.items())}
        </div>
        <div class="legend">
            {''.join(f'<span style="display: inline-block; width: 100px; color: {"red" if tier == "high" else "orange" if tier == "medium" else "green"};">{tier.capitalize()}</span>' for tier in counts)}
        </div>
        <script>
            fetch('/api/risk/tier/summary')
                .then(response => response.json())
                .then(data => {{
                    console.log('Fetched data:', data);
                }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@router.get("/api/risk/tier/summary")
async def get_risk_tier_summary(session: Session = Depends(get_session)):
    """API endpoint returning risk_tier counts as JSON."""
    return get_risk_tier_counts(session)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # Setup in-memory test database
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    # Mock data
    from app.models import McpServerRegistry
    with Session(engine) as session:
        session.add_all([
            McpServerRegistry(server_id="1", name="Server 1", risk_tier="high"),
            McpServerRegistry(server_id="2", name="Server 2", risk_tier="medium"),
            McpServerRegistry(server_id="3", name="Server 3", risk_tier="low"),
            McpServerRegistry(server_id="4", name="Server 4", risk_tier="high"),
        ])
        session.commit()

    # Override dependencies for testing
    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        return Session(engine)

    app.dependency_overrides[get_session] = override_get_session

    # Run tests
    client = TestClient(app)
    response = client.get("/ui/risk/summary")
    assert response.status_code == 200
    assert "Risk Tier Summary" in response.text
    assert "high" in response.text.lower()
    assert "medium" in response.text.lower()
    assert "low" in response.text.lower()
    print("PASS")