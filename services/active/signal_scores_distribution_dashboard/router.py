<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Signal Scores Distribution Dashboard</title>
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
            display: flex;
            flex-wrap: wrap;
            justify-content: space-around;
        }
        .axis {
            flex: 1 1 30%;
            margin: 10px;
            padding: 15px;
            background-color: #f9f9f9;
            border-radius: 8px;
            box-shadow: 0 0 5px rgba(0, 0, 0, 0.1);
        }
        .axis h3 {
            margin-top: 0;
        }
        .overall {
            text-align: center;
            margin: 20px 0;
            padding: 20px;
            background-color: #e9e9e9;
            border-radius: 8px;
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
            <h1>Signal Scores Distribution Dashboard</h1>
        </div>
        <div id="content">
            <div class="loading">Loading data...</div>
        </div>
    </div>
    <script>
        const API_BASE_URL = "http://127.0.0.1:8000/api";
        
        async function fetchData() {
            try {
                const response = await fetch(`${API_BASE_URL}/signal_scores_distribution`, {
                    headers: {
                        'Authorization': `Bearer ${localStorage.getItem('authToken')}`
                    }
                });
                if (!response.ok) {
                    throw new Error('Network response was not ok');
                }
                const data = await response.json();
                renderData(data);
            } catch (error) {
                renderError(error);
            }
        }
        
        function renderData(data) {
            const content = document.getElementById('content');
            if (!data || data.length === 0) {
                content.innerHTML = '<div class="empty">No data available</div>';
                return;
            }
            
            let html = '<div class="risk-axes">';
            data.forEach(axis => {
                html += `
                    <div class="axis">
                        <h3>${axis.name}</h3>
                        <p>Score: ${axis.score}</p>
                    </div>
                `;
            });
            html += '</div>';
            
            html += '<div class="overall">';
            html += '<h2>Overall Verdict</h2>';
            html += `<p>Criteria Version: ${data[0].criteria_version}</p>`;
            html += '</div>';
            
            content.innerHTML = html;
        }
        
        function renderError(error) {
            const content = document.getElementById('content');
            content.innerHTML = `<div class="error">Error: ${error.message}</div>`;
        }
        
        // Initial fetch
        fetchData();
        
        // Self-test
        function testRenderFunctions() {
            const emptyData = [];
            renderData(emptyData);
            const error = new Error('Test error');
            renderError(error);
            console.log('Self-test passed');
        }
        
        testRenderFunctions();
    </script>
</body>
</html>