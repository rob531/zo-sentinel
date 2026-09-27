<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Risk Tier Summary Dashboard</title>
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
        .header {
            text-align: center;
            margin-bottom: 20px;
        }
        .risk-axes {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
            margin-bottom: 20px;
        }
        .risk-axis {
            background-color: #e9ecef;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
        }
        .overall-risk {
            background-color: #e9ecef;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
            margin-bottom: 20px;
        }
        .verdict-tier {
            background-color: #e9ecef;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
            margin-bottom: 20px;
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
        <div class="header">
            <h1>Risk Tier Summary Dashboard</h1>
        </div>
        <div id="loading" class="loading">Loading...</div>
        <div id="error" class="error" style="display: none;">Error loading data</div>
        <div id="empty" class="empty" style="display: none;">No data available</div>
        <div id="content" style="display: none;">
            <div class="risk-axes">
                <div class="risk-axis">
                    <h2>Risk Axis 1</h2>
                    <p id="risk-axis-1">0</p>
                </div>
                <div class="risk-axis">
                    <h2>Risk Axis 2</h2>
                    <p id="risk-axis-2">0</p>
                </div>
                <div class="risk-axis">
                    <h2>Risk Axis 3</h2>
                    <p id="risk-axis-3">0</p>
                </div>
                <div class="risk-axis">
                    <h2>Risk Axis 4</h2>
                    <p id="risk-axis-4">0</p>
                </div>
                <div class="risk-axis">
                    <h2>Risk Axis 5</h2>
                    <p id="risk-axis-5">0</p>
                </div>
                <div class="risk-axis">
                    <h2>Risk Axis 6</h2>
                    <p id="risk-axis-6">0</p>
                </div>
            </div>
            <div class="overall-risk">
                <h2>Overall Risk</h2>
                <p id="overall-risk">0</p>
            </div>
            <div class="verdict-tier">
                <h2>Verdict Tier</h2>
                <p id="verdict-tier">None</p>
            </div>
        </div>
    </div>
    <script>
        const API_BASE_URL = "http://localhost:8000/api";
        
        async function fetchData() {
            try {
                const response = await fetch(`${API_BASE_URL}/risk-tier-summary`);
                if (!response.ok) {
                    throw new Error('Network response was not ok');
                }
                const data = await response.json();
                if (data.length === 0) {
                    document.getElementById('empty').style.display = 'block';
                } else {
                    updateDashboard(data);
                }
            } catch (error) {
                document.getElementById('error').style.display = 'block';
                console.error('Error fetching data:', error);
            } finally {
                document.getElementById('loading').style.display = 'none';
            }
        }
        
        function updateDashboard(data) {
            document.getElementById('risk-axis-1').textContent = data.risk_axis_1;
            document.getElementById('risk-axis-2').textContent = data.risk_axis_2;
            document.getElementById('risk-axis-3').textContent = data.risk_axis_3;
            document.getElementById('risk-axis-4').textContent = data.risk_axis_4;
            document.getElementById('risk-axis-5').textContent = data.risk_axis_5;
            document.getElementById('risk-axis-6').textContent = data.risk_axis_6;
            document.getElementById('overall-risk').textContent = data.overall_risk;
            document.getElementById('verdict-tier').textContent = data.verdict_tier;
            document.getElementById('content').style.display = 'block';
        }
        
        document.addEventListener('DOMContentLoaded', fetchData);
    </script>
</body>
</html>
