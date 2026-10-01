<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Server Risk Tier Comparison Dashboard</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f4f4f4;
        }
        .dashboard {
            max-width: 1200px;
            margin: 0 auto;
            background: #fff;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
        }
        .risk-axis {
            margin-bottom: 20px;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            background: #f9f9f9;
        }
        .risk-axis h3 {
            margin-top: 0;
        }
        .risk-axis p {
            margin: 5px 0;
        }
        .loading, .error, .empty {
            text-align: center;
            padding: 20px;
            font-size: 18px;
            color: #666;
        }
    </style>
</head>
<body>
    <div class="dashboard">
        <h1>Server Risk Tier Comparison Dashboard</h1>
        <div id="risk-axes" aria-label="Risk Axes">
            <!-- Risk axes will be loaded here -->
        </div>
        <div id="loading" class="loading">Loading data...</div>
        <div id="error" class="error" style="display: none;">Error loading data.</div>
        <div id="empty" class="empty" style="display: none;">No data available.</div>
    </div>
    <script>
        const API_BASE_URL = "http://localhost:8000/api";
        
        async function fetchRiskData() {
            try {
                const response = await fetch(`${API_BASE_URL}/verdict-breakdown`, {
                    headers: {
                        'Authorization': `Bearer ${localStorage.getItem('authToken')}`
                    }
                });
                if (!response.ok) {
                    throw new Error('Network response was not ok');
                }
                const data = await response.json();
                renderRiskData(data);
            } catch (error) {
                document.getElementById('loading').style.display = 'none';
                document.getElementById('error').style.display = 'block';
                console.error('Error fetching risk data:', error);
            }
        }
        
        function renderRiskData(data) {
            document.getElementById('loading').style.display = 'none';
            if (!data || data.length === 0) {
                document.getElementById('empty').style.display = 'block';
                return;
            }
            
            const riskAxesContainer = document.getElementById('risk-axes');
            riskAxesContainer.innerHTML = '';
            
            data.forEach(axis => {
                const axisElement = document.createElement('div');
                axisElement.className = 'risk-axis';
                axisElement.innerHTML = `
                    <h3>${axis.name}</h3>
                    <p>Score: ${axis.score}</p>
                    <p>Verdict: ${axis.verdict}</p>
                `;
                riskAxesContainer.appendChild(axisElement);
            });
        }
        
        // Self-check block
        function selfCheck() {
            const testData = [];
            renderRiskData(testData);
            console.assert(document.getElementById('empty').style.display === 'block', 'Self-test failed: Empty state not rendered correctly');
        }
        
        document.addEventListener('DOMContentLoaded', () => {
            fetchRiskData();
            selfCheck();
        });
    </script>
</body>
</html>