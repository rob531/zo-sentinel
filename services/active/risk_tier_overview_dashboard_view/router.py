<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Risk Tier Overview Dashboard</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 0;
            background-color: #f4f4f4;
        }
        .dashboard {
            max-width: 1200px;
            margin: 20px auto;
            padding: 20px;
            background-color: #fff;
            border-radius: 8px;
            box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
        }
        .risk-axis {
            margin-bottom: 20px;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            background-color: #f9f9f9;
        }
        .risk-axis h3 {
            margin-top: 0;
        }
        .risk-axis .score {
            font-weight: bold;
        }
        .overall-risk {
            margin-top: 20px;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            background-color: #e9e9e9;
        }
        .verdict-tier {
            margin-top: 20px;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            background-color: #f9f9f9;
        }
        .loading, .error, .empty {
            text-align: center;
            padding: 20px;
            font-size: 18px;
        }
    </style>
</head>
<body>
    <div class="dashboard">
        <h1>Risk Tier Overview Dashboard</h1>
        <div id="risk-axes">
            <!-- Risk axes will be populated here -->
        </div>
        <div class="overall-risk">
            <h2>Overall Risk</h2>
            <div id="overall-risk-score"></div>
        </div>
        <div class="verdict-tier">
            <h2>Verdict Tier</h2>
            <div id="verdict-tier"></div>
        </div>
        <div id="loading" class="loading">Loading...</div>
        <div id="error" class="error" style="display: none;">Error loading data</div>
        <div id="empty" class="empty" style="display: none;">No data available</div>
    </div>
    <script>
        const API_BASE_URL = "http://localhost:8000/api";
        
        async function fetchData() {
            try {
                const response = await fetch(`${API_BASE_URL}/risk-tier-overview`, {
                    headers: {
                        'Authorization': `Bearer ${localStorage.getItem('authToken')}`
                    }
                });
                
                if (!response.ok) {
                    throw new Error('Network response was not ok');
                }
                
                const data = await response.json();
                
                if (data.length === 0) {
                    document.getElementById('empty').style.display = 'block';
                    document.getElementById('loading').style.display = 'none';
                    return;
                }
                
                renderData(data);
            } catch (error) {
                document.getElementById('error').style.display = 'block';
                document.getElementById('loading').style.display = 'none';
                console.error('Error fetching data:', error);
            }
        }
        
        function renderData(data) {
            const riskAxesContainer = document.getElementById('risk-axes');
            riskAxesContainer.innerHTML = '';
            
            data.risk_axes.forEach(axis => {
                const axisElement = document.createElement('div');
                axisElement.className = 'risk-axis';
                axisElement.innerHTML = `
                    <h3>${axis.name}</h3>
                    <div class="score">Score: ${axis.score}</div>
                `;
                riskAxesContainer.appendChild(axisElement);
            });
            
            document.getElementById('overall-risk-score').textContent = `Overall Risk Score: ${data.overall_risk_score}`;
            document.getElementById('verdict-tier').textContent = `Verdict Tier: ${data.verdict_tier}`;
            
            document.getElementById('loading').style.display = 'none';
        }
        
        // Initial fetch
        fetchData();
        
        // Self-test
        function testRenderData() {
            const testData = {
                risk_axes: [
                    { name: 'Axis 1', score: 75 },
                    { name: 'Axis 2', score: 80 }
                ],
                overall_risk_score: 77.5,
                verdict_tier: 'Medium'
            };
            
            renderData(testData);
            
            // Verify the rendered content
            const riskAxes = document.querySelectorAll('.risk-axis');
            assert(riskAxes.length === 2, 'Risk axes count mismatch');
            assert(document.getElementById('overall-risk-score').textContent.includes('77.5'), 'Overall risk score mismatch');
            assert(document.getElementById('verdict-tier').textContent.includes('Medium'), 'Verdict tier mismatch');
            
            console.log('Self-test passed');
        }
        
        // Run self-test
        testRenderData();
    </script>
</body>
</html>