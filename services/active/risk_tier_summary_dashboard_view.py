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
            display: flex;
            flex-wrap: wrap;
            justify-content: space-around;
        }
        .risk-axis {
            flex: 1 1 300px;
            margin: 10px;
            padding: 20px;
            background-color: #f9f9f9;
            border-radius: 8px;
            box-shadow: 0 0 5px rgba(0, 0, 0, 0.1);
        }
        .risk-axis h3 {
            margin-top: 0;
        }
        .verdict-tier {
            margin-top: 20px;
            padding: 10px;
            background-color: #e9e9e9;
            border-radius: 4px;
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
        <div id="risk-axes" class="risk-axes">
            <!-- Risk axes will be populated by JavaScript -->
        </div>
        <div id="verdict-tier" class="verdict-tier">
            <!-- Verdict tier will be populated by JavaScript -->
        </div>
        <div id="loading" class="loading">Loading...</div>
        <div id="error" class="error" style="display: none;">Error loading data</div>
        <div id="empty" class="empty" style="display: none;">No data available</div>
    </div>
    <script>
        const API_BASE_URL = "http://localhost:8000/api";
        
        async function fetchData() {
            try {
                const response = await fetch(`${API_BASE_URL}/verdict-breakdown`, {
                    headers: {
                        "Authorization": `Bearer ${localStorage.getItem("authToken")}`
                    }
                });
                
                if (!response.ok) {
                    throw new Error("Network response was not ok");
                }
                
                const data = await response.json();
                
                if (data.length === 0) {
                    document.getElementById("empty").style.display = "block";
                    document.getElementById("loading").style.display = "none";
                    return;
                }
                
                renderRiskAxes(data);
                renderVerdictTier(data);
                
                document.getElementById("loading").style.display = "none";
            } catch (error) {
                document.getElementById("error").style.display = "block";
                document.getElementById("loading").style.display = "none";
                console.error("Error fetching data:", error);
            }
        }
        
        function renderRiskAxes(data) {
            const riskAxesContainer = document.getElementById("risk-axes");
            riskAxesContainer.innerHTML = "";
            
            data.forEach(axis => {
                const axisElement = document.createElement("div");
                axisElement.className = "risk-axis";
                axisElement.innerHTML = `
                    <h3>${axis.name}</h3>
                    <p>Score: ${axis.score}</p>
                `;
                riskAxesContainer.appendChild(axisElement);
            });
        }
        
        function renderVerdictTier(data) {
            const verdictTierContainer = document.getElementById("verdict-tier");
            const overallScore = data.reduce((sum, axis) => sum + axis.score, 0) / data.length;
            
            verdictTierContainer.innerHTML = `
                <h3>Overall Verdict Tier</h3>
                <p>Score: ${overallScore.toFixed(2)}</p>
            `;
        }
        
        // Self-test
        function testRenderFunctions() {
            const testData = [];
            renderRiskAxes(testData);
            renderVerdictTier(testData);
            console.log("Self-test passed");
        }
        
        // Initialize the dashboard
        document.addEventListener("DOMContentLoaded", () => {
            fetchData();
            testRenderFunctions();
        });
    </script>
</body>
</html>