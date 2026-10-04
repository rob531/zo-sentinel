<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Definition History Pipeline Status Dashboard</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f4f4f9;
        }
        .dashboard {
            max-width: 1200px;
            margin: 0 auto;
            background-color: #fff;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
        }
        .header {
            text-align: center;
            margin-bottom: 20px;
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
        .risk-axis p {
            margin: 5px 0;
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
            <h1>Definition History Pipeline Status Dashboard</h1>
        </div>
        <div id="risk-axes"></div>
        <div id="loading" class="loading">Loading data...</div>
        <div id="error" class="error" style="display: none;">Error loading data.</div>
        <div id="empty" class="empty" style="display: none;">No data available.</div>
    </div>
    <script>
        const API_BASE_URL = "http://localhost:8000/api";
        
        async function fetchData() {
            try {
                const response = await fetch(`${API_BASE_URL}/definition_history_pipeline_status`);
                if (!response.ok) {
                    throw new Error('Network response was not ok');
                }
                const data = await response.json();
                renderData(data);
            } catch (error) {
                document.getElementById('loading').style.display = 'none';
                document.getElementById('error').style.display = 'block';
                console.error('Error fetching data:', error);
            }
        }
        
        function renderData(data) {
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
                    <p><strong>Status:</strong> ${axis.status}</p>
                    <p><strong>Last Updated:</strong> ${axis.lastUpdated}</p>
                `;
                riskAxesContainer.appendChild(axisElement);
            });
        }
        
        // Self-check block
        function selfCheck() {
            const testData = [];
            renderData(testData);
            console.assert(document.getElementById('empty').style.display === 'block', 'Self-check failed: Empty state not handled correctly');
        }
        
        // Initial fetch
        fetchData();
        selfCheck();
    </script>
</body>
</html>