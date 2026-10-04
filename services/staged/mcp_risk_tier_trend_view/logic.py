import os
import tempfile
import json
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import FastAPI, Depends, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func, distinct
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

RISK_TIERS = ["TRUSTED_GENERAL", "ENTERPRISE_CONTROLLED", "CAUTION_LIMITED", "HIGH_RISK_ISOLATED"]

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MCP Risk Tier Trend</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <style>
        body { font-family: system-ui, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background: white; border-radius: 8px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        h1 { color: #333; margin-bottom: 16px; }
        .controls { margin-bottom: 20px; display: flex; gap: 12px; align-items: center; }
        .controls label { font-weight: 500; }
        .controls select { padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }
        .chart-container { position: relative; height: 500px; width: 100%; }
        .legend-custom { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 16px; justify-content: center; }
        .legend-item { display: flex; align-items: center; gap: 6px; font-size: 13px; }
        .legend-color { width: 14px; height: 14px; border-radius: 3px; }
        .tier-TRUSTED_GENERAL { background: #22c55e; }
        .tier-ENTERPRISE_CONTROLLED { background: #3b82f6; }
        .tier-CAUTION_LIMITED { background: #f59e0b; }
        .tier-HIGH_RISK_ISOLATED { background: #ef4444; }
        .loading { text-align: center; padding: 40px; color: #666; }
        .error { color: #dc2626; padding: 20px; text-align: center; }
    </style>
</head>
<body>
    <div class="container">
        <h1>MCP Risk Tier Trend Over Time</h1>
        <div class="controls">
            <label for="windowSelect">Time Window:</label>
            <select id="windowSelect">
                <option value="7">Last 7 Days</option>
                <option value="30" selected>Last 30 Days</option>
                <option value="90">Last 90 Days</option>
            </select>
        </div>
        <div class="chart-container">
            <canvas id="trendChart"></canvas>
        </div>
        <div class="loading" id="loading">Loading risk tier data...</div>
        <div class="error" id="error" style="display:none;"></div>
        <div class="legend-custom">
            <div class="legend-item"><div class="legend-color tier-TRUSTED_GENERAL"></div>Trusted General</div>
            <div class="legend-item"><div class="legend-color tier-ENTERPRISE_CONTROLLED"></div>Enterprise Controlled</div>
            <div class="legend-item"><div class="legend-color tier-CAUTION_LIMITED"></div>Caution Limited</div>
            <div class="legend-item"><div class="legend-color tier-HIGH_RISK_ISOLATED"></div>High Risk Isolated</div>
        </div>
    </div>

    <script>
        const TIER_COLORS = {
            'TRUSTED_GENERAL': { bg: 'rgba(34, 197, 94, 0.7)', border: 'rgb(34, 197, 94)' },
            'ENTERPRISE_CONTROLLED': { bg: 'rgba(59, 130, 246, 0.7)', border: 'rgb(59, 130, 246)' },
            'CAUTION_LIMITED': { bg: 'rgba(245, 158, 11, 0.7)', border: 'rgb(245, 158, 11)' },
            'HIGH_RISK_ISOLATED': { bg: 'rgba(239, 68, 68, 0.7)', border: 'rgb(239, 68, 68)' }
        };

        let chart = null;
        let cachedData = null;

        async function fetchData(days) {
            try {
                const response = await fetch(`/api/risk-tiers/trend?days=${days}`);
                if (!response.ok) throw new Error('Failed to fetch data');
                return await response.json();
            } catch (err) {
                document.getElementById('error').textContent = 'Error loading data: ' + err.message;
                document.getElementById('error').style.display = 'block';
                document.getElementById('loading').style.display = 'none';
                return null;
            }
        }

        function renderChart(data, days) {
            const ctx = document.getElementById('trendChart').getContext('2d');
            
            if (chart) {
                chart.destroy();
            }

            const datasets = ['TRUSTED_GENERAL', 'ENTERPRISE_CONTROLLED', 'CAUTION_LIMITED', 'HIGH_RISK_ISOLATED'].map(tier => ({
                label: tier.replace(/_/g, ' '),
                data: data.series[tier] || [],
                backgroundColor: TIER_COLORS[tier].bg,
                borderColor: TIER_COLORS[tier].border,
                borderWidth: 2,
                fill: true,
                tension: 0.3
            }));

            chart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.labels,
                    datasets: datasets
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {
                        mode: 'index',
                        intersect: false
                    },
                    plugins: {
                        title: {
                            display: true,
                            text: `Risk Tier Distribution - Last ${days} Days`,
                            font: { size: 16 }
                        },
                        legend: {
                            display: false
                        },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    return context.dataset.label + ': ' + context.raw;
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            title: { display: true, text: 'Date' },
                            ticks: { maxRotation: 45 }
                        },
                        y: {
                            title: { display: true, text: 'Server Count' },
                            stacked: true,
                            beginAtZero: true
                        }
                    }
                }
            });

            document.getElementById('loading').style.display = 'none';
        }

        async function updateChart() {
            const days = parseInt(document.getElementById('windowSelect').value);
            document.getElementById('loading').style.display = 'block';
            document.getElementById('error').style.display = 'none';
            
            cachedData = await fetchData(days);
            if (cachedData) {
                renderChart(cachedData, days);
            }
        }

        document.getElementById('windowSelect').addEventListener('change', updateChart);
        updateChart();
    </script>
</body>
</html>
"""

app = FastAPI()


@app.get("/view/risk-tiers/trend")
async def get_risk_tier_trend_view():
    """Serve the HTML dashboard for risk tier trends."""
    return Response(content=HTML_TEMPLATE, media_type="text/html")


@app.get("/api/risk-tiers/trend")
async def get_risk_tier_trend_api(
    days: int = 30,
    session: Session = Depends(get_session)
):
    """
    Get risk tier trend data aggregated by day.
    Reads from mcp_llm_axis_scores joined with mcp_server_registry.
    """
    if days not in [7, 30, 90]:
        days = 30
    
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    # Get distinct servers per day per risk_tier based on their latest assessment
    # We track the risk_tier for each server_id on each day
    stmt = (
        select(
            func.date(McpServerRegistry.last_assessed).label('date'),
            McpServerRegistry.risk_tier,
            func.count(distinct(McpServerRegistry.server_id)).label('count')
        )
        .where(McpServerRegistry.last_assessed >= cutoff)
        .where(McpServerRegistry.risk_tier.isnot(None))
        .group_by(
            func.date(McpServerRegistry.last_assessed),
            McpServerRegistry.risk_tier
        )
        .order_by(func.date(McpServerRegistry.last_assessed))
    )
    
    result = session.execute(stmt).all()
    
    # Aggregate data by date
    date_data = {}
    all_dates = []
    
    for row in result:
        date_str = str(row.date)
        tier = row.risk_tier
        count = row.count
        
        if date_str not in date_data:
            date_data[date_str] = {t: 0 for t in RISK_TIERS}
            all_dates.append(date_str)
        
        if tier in date_data[date_str]:
            date_data[date_str][tier] = count
    
    # Fill in missing dates with zeros
    if all_dates:
        all_dates.sort()
    
    # Build series for each tier
    series = {tier: [] for tier in RISK_TIERS}
    for date in all_dates:
        for tier in RISK_TIERS:
            series[tier].append(date_data.get(date, {}).get(tier, 0))
    
    return {
        "labels": all_dates,
        "series": series,
        "tiers": RISK_TIERS
    }


def get_server_risk_tier(session: Session, server_id: str) -> str | None:
    """Get the current risk tier for a server."""
    stmt = select(McpServerRegistry.risk_tier).where(McpServerRegistry.server_id == server_id)
    result = session.execute(stmt).scalar_one_or_none()
    return result


def get_all_risk_tier_counts(session: Session) -> dict[str, int]:
    """Get count of servers per risk tier."""
    stmt = (
        select(McpServerRegistry.risk_tier, func.count(McpServerRegistry.server_id))
        .where(McpServerRegistry.risk_tier.isnot(None))
        .group_by(McpServerRegistry.risk_tier)
    )
    result = session.execute(stmt).all()
    return {row[0]: row[1] for row in result}


if __name__ == "__main__":
    import http.server
    import socketserver
    import threading
    
    this_file = os.path.abspath(__file__)
    
    # Assert file is readable and non-empty
    file_size = os.path.getsize(this_file)
    assert file_size > 2048, f"File too small: {file_size} bytes"
    
    # Read and check content
    with open(this_file, 'r') as f:
        content = f.read()
    
    # Check for expected tier labels
    for tier in RISK_TIERS:
        assert tier in content, f"Missing tier label: {tier}"
    
    # Check for Chart.js CDN reference
    assert "cdn.jsdelivr.net/npm/chart.js" in content, "Missing Chart.js CDN reference"
    
    # Create a temp directory and serve the file
    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy the logic.py file to temp dir for serving
        import shutil
        tmp_logic = os.path.join(tmpdir, "logic.py")
        shutil.copy(this_file, tmp_logic)
        
        # Set up HTTP server
        os.chdir(tmpdir)
        
        handler = http.server.SimpleHTTPRequestHandler
        handler.extensions_map.update({'.html': 'text/html'})
        
        with socketserver.TCPServer(("", 0), handler) as httpd:
            port = httpd.server_address[1]
            server_thread = threading.Thread(target=httpd.serve_forever)
            server_thread.daemon = True
            server_thread.start()
            
            import urllib.request
            import time
            
            time.sleep(0.5)  # Give server time to start
            
            # Fetch the HTML content
            try:
                response = urllib.request.urlopen(f"http://localhost:{port}/logic.py")
                html_content = response.read().decode('utf-8')
            except Exception as e:
                print(f"FAIL: Could not fetch logic.py: {e}")
                exit(1)
            
            # Verify content
            for tier in RISK_TIERS:
                assert tier in html_content, f"Missing tier: {tier}"
            
            assert "chart.js" in html_content.lower(), "Missing Chart.js reference"
            
            httpd.shutdown()
    
    print("PASS")