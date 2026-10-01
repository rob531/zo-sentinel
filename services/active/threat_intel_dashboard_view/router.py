<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Threat Intel Dashboard</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 0;
            background-color: #f4f4f4;
        }
        .dashboard {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
            padding: 20px;
        }
        .card {
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
            padding: 20px;
            margin-bottom: 20px;
        }
        .card h2 {
            margin-top: 0;
            color: #333;
        }
        .risk-axis {
            margin-bottom: 10px;
        }
        .risk-axis h3 {
            margin: 0;
            color: #555;
        }
        .risk-axis .value {
            font-size: 24px;
            font-weight: bold;
        }
        .overall {
            grid-column: 1 / -1;
            text-align: center;
        }
        .overall h2 {
            color: #333;
        }
        .overall .value {
            font-size: 48px;
            font-weight: bold;
        }
        .loading, .error {
            text-align: center;
            padding: 20px;
            font-size: 18px;
            color: #666;
        }
    </style>
</head>
<body>
    <div class="dashboard" id="dashboard">
        <div class="card overall">
            <h2>Overall Risk</h2>
            <div class="value" id="overall-risk">Loading...</div>
        </div>
        <div class="card">
            <h2>Risk Axes</h2>
            <div class="risk-axis">
                <h3>Exploit Surface</h3>
                <div class="value" id="exploit-surface">Loading...</div>
            </div>
            <div class="risk-axis">
                <h3>Network Egress</h3>
                <div class="value" id="network-egress">Loading...</div>
            </div>
            <div class="risk-axis">
                <h3>Signal Drift</h3>
                <div class="value" id="signal-drift">Loading...</div>
            </div>
            <div class="risk-axis">
                <h3>Axis Calibration</h3>
                <div class="value" id="axis-calibration">Loading...</div>
            </div>
            <div class="risk-axis">
                <h3>Axis Direction</h3>
                <div class="value" id="axis-direction">Loading...</div>
            </div>
            <div class="risk-axis">
                <h3>Axis Divergence</h3>
                <div class="value" id="axis-divergence">Loading...</div>
            </div>
        </div>
    </div>
    <script>
        const API_BASE_URL = "http://localhost:8000/api";
        
        async function fetchData() {
            try {
                const response = await fetch(`${API_BASE_URL}/risk_axes`);
                if (!response.ok) {
                    throw new Error('Network response was not ok');
                }
                const data = await response.json();
                updateDashboard(data);
            } catch (error) {
                console.error('Error fetching data:', error);
                document.getElementById('dashboard').innerHTML = '<div class="error">Failed to load data</div>';
            }
        }
        
        function updateDashboard(data) {
            if (!data || Object.keys(data).length === 0) {
                document.getElementById('dashboard').innerHTML = '<div class="error">No data available</div>';
                return;
            }
            
            document.getElementById('overall-risk').textContent = data.overall || 'N/A';
            document.getElementById('exploit-surface').textContent = data.exploit_surface || 'N/A';
            document.getElementById('network-egress').textContent = data.network_egress || 'N/A';
            document.getElementById('signal-drift').textContent = data.signal_drift || 'N/A';
            document.getElementById('axis-calibration').textContent = data.axis_calibration || 'N/A';
            document.getElementById('axis-direction').textContent = data.axis_direction || 'N/A';
            document.getElementById('axis-divergence').textContent = data.axis_divergence || 'N/A';
        }
        
        // Initial fetch
        fetchData();
        
        // Self-test
        function testUpdateDashboard() {
            const testData = {
                overall: 'High',
                exploit_surface: 'Medium',
                network_egress: 'Low',
                signal_drift: 'High',
                axis_calibration: 'Medium',
                axis_direction: 'Low',
                axis_divergence: 'Medium'
            };
            updateDashboard(testData);
            console.log('Self-test passed');
        }
        
        testUpdateDashboard();
    </script>
</body>
</html>