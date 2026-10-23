"""
Router for server axis probability summary view.
Serves a self-contained HTML dashboard showing per-axis probability distributions.
"""
from typing import Annotated
from fastapi import APIRouter, Depends, Path
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/ui/servers", tags=["ui"])


@router.get("/{server_id}/axis-probabilities", response_class=HTMLResponse)
async def get_server_axis_probabilities_ui(
    server_id: Annotated[str, Path(title="Server ID")],
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """
    Render a self-contained HTML dashboard for server axis probability distributions.
    The HTML includes inline JavaScript that fetches probability data from the API.
    """
    # Fetch axis score data for the server
    stmt = (
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    )
    result = session.execute(stmt)
    latest_score = result.scalar_one_or_none()

    html_content = _build_html(server_id, latest_score)
    return HTMLResponse(content=html_content)


def _build_html(server_id: str, score: McpLlmAxisScore | None) -> str:
    """Build the self-contained HTML dashboard with inline JS and CSS."""
    if score is None:
        return _build_empty_html(server_id)

    # Collect axis data for JavaScript
    axis_data_lines = []
    # We need to fetch all axes for the server
    from sqlalchemy import select
    stmt = select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    results = session.execute(stmt) if 'session' in dir() else []
    
    # Default single score data if no batch available
    single_axis_data = {
        "axis_name": score.axis_name,
        "label": score.label,
        "label_index": score.label_index,
        "probs": score.probs or [],
        "p_top": score.p_top,
        "p_critical": score.p_critical,
        "p_danger": score.p_danger,
        "escalated": score.escalated,
        "scored_at": str(score.scored_at) if score.scored_at else None,
    }

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Axis Probability Summary - {server_id}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #e0e0e0;
            padding: 20px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .header {{
            text-align: center;
            margin-bottom: 30px;
        }}
        .header h1 {{
            color: #00d9ff;
            font-size: 2em;
            margin-bottom: 10px;
        }}
        .header .server-id {{
            color: #888;
            font-family: monospace;
            font-size: 0.9em;
        }}
        .chart-container {{
            background: rgba(255, 255, 255, 0.05);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        }}
        .chart-wrapper {{
            position: relative;
            height: 500px;
            display: flex;
            justify-content: center;
            align-items: center;
        }}
        canvas {{
            max-width: 100%;
            max-height: 100%;
        }}
        .axis-details {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 16px;
            margin-top: 20px;
        }}
        .axis-card {{
            background: rgba(255, 255, 255, 0.08);
            border-radius: 12px;
            padding: 16px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }}
        .axis-card h3 {{
            color: #00d9ff;
            margin-bottom: 12px;
            font-size: 1.1em;
        }}
        .prob-bar {{
            display: flex;
            align-items: center;
            margin: 8px 0;
        }}
        .prob-label {{
            width: 100px;
            font-size: 0.85em;
            color: #aaa;
        }}
        .prob-track {{
            flex: 1;
            height: 20px;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 10px;
            overflow: hidden;
            margin: 0 10px;
        }}
        .prob-fill {{
            height: 100%;
            border-radius: 10px;
            transition: width 0.5s ease;
        }}
        .prob-value {{
            width: 60px;
            text-align: right;
            font-family: monospace;
            font-size: 0.9em;
        }}
        .p-top {{ background: linear-gradient(90deg, #00ff88, #00cc6a); }}
        .p-critical {{ background: linear-gradient(90deg, #ff6b6b, #ee5a5a); }}
        .p-danger {{ background: linear-gradient(90deg, #ffd93d, #f0c419); }}
        .loading {{
            text-align: center;
            padding: 40px;
            color: #888;
        }}
        .error {{
            background: rgba(255, 107, 107, 0.2);
            border: 1px solid #ff6b6b;
            border-radius: 8px;
            padding: 20px;
            text-align: center;
            color: #ff6b6b;
        }}
        .timestamp {{
            text-align: center;
            color: #666;
            font-size: 0.85em;
            margin-top: 20px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Axis Probability Distribution</h1>
            <div class="server-id">Server: {server_id}</div>
        </div>
        
        <div class="chart-container">
            <div class="chart-wrapper">
                <canvas id="radarChart" width="500" height="500"></canvas>
            </div>
        </div>
        
        <div class="axis-details" id="axisDetails">
            <div class="loading">Loading axis details...</div>
        </div>
        
        <div class="timestamp" id="timestamp"></div>
    </div>
    
    <script>
        const SERVER_ID = "{server_id}";
        let chartInstance = null;
        
        async function fetchAxisData() {{
            try {{
                const response = await fetch(`/api/servers/${{SERVER_ID}}/axis-probabilities`);
                if (!response.ok) {{
                    throw new Error(`HTTP ${{response.status}}`);
                }}
                const data = await response.json();
                return data;
            }} catch (error) {{
                console.error('Failed to fetch axis data:', error);
                return null;
            }}
        }}
        
        function drawRadarChart(axisData) {{
            const canvas = document.getElementById('radarChart');
            const ctx = canvas.getContext('2d');
            const centerX = canvas.width / 2;
            const centerY = canvas.height / 2;
            const maxRadius = Math.min(centerX, centerY) - 60;
            
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            
            // Draw concentric circles
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
            ctx.lineWidth = 1;
            for (let i = 1; i <= 5; i++) {{
                ctx.beginPath();
                ctx.arc(centerX, centerY, (maxRadius / 5) * i, 0, Math.PI * 2);
                ctx.stroke();
            }}
            
            // Draw axes
            const axes = axisData.axes || [];
            const numAxes = axes.length;
            
            if (numAxes === 0) return;
            
            const angleStep = (Math.PI * 2) / numAxes;
            
            axes.forEach((axis, index) => {{
                const angle = index * angleStep - Math.PI / 2;
                const x = centerX + Math.cos(angle) * maxRadius;
                const y = centerY + Math.sin(angle) * maxRadius;
                
                // Draw axis line
                ctx.beginPath();
                ctx.moveTo(centerX, centerY);
                ctx.lineTo(x, y);
                ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
                ctx.stroke();
                
                // Draw label
                ctx.fillStyle = '#00d9ff';
                ctx.font = '12px sans-serif';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                const labelX = centerX + Math.cos(angle) * (maxRadius + 30);
                const labelY = centerY + Math.sin(angle) * (maxRadius + 30);
                ctx.fillText(axis.axis_name || `Axis ${{index}}`, labelX, labelY);
                
                // Draw probability point
                const probValue = axis.p_top || 0.5;
                const pointX = centerX + Math.cos(angle) * (maxRadius * probValue);
                const pointY = centerY + Math.sin(angle) * (maxRadius * probValue);
                
                ctx.beginPath();
                ctx.arc(pointX, pointY, 6, 0, Math.PI * 2);
                ctx.fillStyle = '#00ff88';
                ctx.fill();
                ctx.strokeStyle = '#fff';
                ctx.lineWidth = 2;
                ctx.stroke();
            }});
            
            // Draw polygon connecting points
            ctx.beginPath();
            axes.forEach((axis, index) => {{
                const angle = index * angleStep - Math.PI / 2;
                const probValue = axis.p_top || 0.5;
                const x = centerX + Math.cos(angle) * (maxRadius * probValue);
                const y = centerY + Math.sin(angle) * (maxRadius * probValue);
                
                if (index === 0) {{
                    ctx.moveTo(x, y);
                }} else {{
                    ctx.lineTo(x, y);
                }}
            }});
            ctx.closePath();
            ctx.fillStyle = 'rgba(0, 217, 255, 0.3)';
            ctx.fill();
            ctx.strokeStyle = '#00d9ff';
            ctx.lineWidth = 2;
            ctx.stroke();
        }}
        
        function renderAxisDetails(axisData) {{
            const container = document.getElementById('axisDetails');
            const axes = axisData.axes || [];
            
            if (axes.length === 0) {{
                container.innerHTML = '<div class="error">No axis data available</div>';
                return;
            }}
            
            container.innerHTML = axes.map(axis => `
                <div class="axis-card">
                    <h3>${{axis.axis_name || 'Unknown Axis'}}</h3>
                    <div class="prob-bar">
                        <span class="prob-label">Top</span>
                        <div class="prob-track">
                            <div class="prob-fill p-top" style="width: ${{(axis.p_top || 0) * 100}}%"></div>
                        </div>
                        <span class="prob-value">${{((axis.p_top || 0) * 100).toFixed(1)}}%</span>
                    </div>
                    <div class="prob-bar">
                        <span class="prob-label">Critical</span>
                        <div class="prob-track">
                            <div class="prob-fill p-critical" style="width: ${{(axis.p_critical || 0) * 100}}%"></div>
                        </div>
                        <span class="prob-value">${{((axis.p_critical || 0) * 100).toFixed(1)}}%</span>
                    </div>
                    <div class="prob-bar">
                        <span class="prob-label">Danger</span>
                        <div class="prob-track">
                            <div class="prob-fill p-danger" style="width: ${{(axis.p_danger || 0) * 100}}%"></div>
                        </div>
                        <span class="prob-value">${{((axis.p_danger || 0) * 100).toFixed(1)}}%</span>
                    </div>
                    ${{axis.escalated ? '<div style="color: #ff6b6b; margin-top: 8px;">⚠ ESCALATED</div>' : ''}}
                </div>
            `).join('');
            
            if (axisData.scored_at) {{
                document.getElementById('timestamp').textContent = 
                    `Last updated: ${{new Date(axisData.scored_at).toLocaleString()}}`;
            }}
        }}
        
        async function init() {{
            const data = await fetchAxisData();
            
            if (!data || !data.axes || data.axes.length === 0) {{
                document.getElementById('axisDetails').innerHTML = 
                    '<div class="error">No axis probability data found for this server</div>';
                return;
            }}
            
            drawRadarChart(data);
            renderAxisDetails(data);
        }}
        
        // Initialize on load
        document.addEventListener('DOMContentLoaded', init);
    </script>
</body>
</html>"""


def _build_empty_html(server_id: str) -> str:
    """Build HTML for when no data is available."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Axis Probability Summary - {server_id}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            color: #e0e0e0;
            margin: 0;
            padding: 20px;
        }}
        .container {{
            text-align: center;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 16px;
            padding: 40px;
            max-width: 500px;
        }}
        h1 {{
            color: #00d9ff;
            margin-bottom: 20px;
        }}
        .error {{
            background: rgba(255, 107, 107, 0.2);
            border: 1px solid #ff6b6b;
            border-radius: 8px;
            padding: 20px;
            color: #ff6b6b;
        }}
        .server-id {{
            color: #888;
            font-family: monospace;
            margin-top: 10px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Axis Probability Summary</h1>
        <div class="error">
            <p>No axis probability data available</p>
            <p>for server: <span class="server-id">{server_id}</span></p>
        </div>
    </div>
</body>
</html>"""


if __name__ == "__main__":
    import subprocess
    result = subprocess.run(
        ["python", "-m", "py_compile", __file__],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("PASS")
    else:
        print(f"FAIL: {result.stderr}")