<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MCP Risk Tier Trend Dashboard</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .dashboard {
            max-width: 1200px;
            margin: 0 auto;
            background-color: white;
            padding: 20px;
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
            background-color: #f9f9f9;
            padding: 15px;
            border-radius: 5px;
            box-shadow: 0 0 5px rgba(0, 0, 0, 0.1);
        }
        .overall-risk {
            background-color: #e9f7fe;
            padding: 20px;
            border-radius: 5px;
            box-shadow: 0 0 5px rgba(0, 0, 0, 0.1);
            margin-bottom: 20px;
        }
        .verdict-tier {
            background-color: #f0f8ff;
            padding: 20px;
            border-radius: 5px;
            box-shadow: 0 0 5px rgba(0, 0, 0, 0.1);
            margin-bottom: 20px;
        }
        .loading, .error, .empty {
            text-align: center;
            padding: 20px;
            font-size: 18px;
        }
        .hidden {
            display: none;
        }
    </style>
</head>
<body>
    <div class="dashboard">
        <div class="header">
            <h1>MCP Risk Tier Trend Dashboard</h1>
        </div>
        <div class="loading" id="loading">Loading data...</div>
        <div class="error hidden" id="error">Error loading data. Please try again later.</div>
        <div class="empty hidden" id="empty">No data available.</div>
        <div class="overall-risk hidden" id="overall-risk">
            <h2>Overall Risk</h2>
            <div id="overall-risk-value"></div>
        </div>
        <div class="risk-axes hidden" id="risk-axes">
            <div class="risk-axis">
                <h3>Axis 1</h3>
                <div id="axis1-value"></div>
            </div>
            <div class="risk-axis">
                <h3>Axis 2</h3>
                <div id="axis2-value"></div>
            </div>
            <div class="risk-axis">
                <h3>Axis 3</h3>
                <div id="axis3-value"></div>
            </div>
            <div class="risk-axis">
                <h3>Axis 4</h3>
                <div id="axis4-value"></div>
            </div>
            <div class="risk-axis">
                <h3>Axis 5</h3>
                <div id="axis5-value"></div>
            </div>
            <div class="risk-axis">
                <h3>Axis 6</h3>
                <div id="axis6-value"></div>
            </div>
        </div>
        <div class="verdict-tier hidden" id="verdict-tier">
            <h2>Verdict Tier</h2>
            <div id="verdict-tier-value"></div>
            <div id="criteria-version"></div>
        </div>
    </div>
    <script>
        const API_BASE_URL = "http://localhost:8000/api";
        const authToken = "your-auth-token"; // Replace with actual auth token
        
        async function fetchData() {
            try {
                const response = await fetch(`${API_BASE_URL}/mcp_risk_tier_trend`, {
                    headers: {
                        "Authorization": `Bearer ${authToken}`
                    }
                });
                
                if (!response.ok) {
                    throw new Error("Network response was not ok");
                }
                
                const data = await response.json();
                
                if (data.length === 0) {
                    document.getElementById("empty").classList.remove("hidden");
                    document.getElementById("loading").classList.add("hidden");
                    return;
                }
                
                renderData(data);
            } catch (error) {
                document.getElementById("error").classList.remove("hidden");
                document.getElementById("loading").classList.add("hidden");
                console.error("Error fetching data:", error);
            }
        }
        
        function renderData(data) {
            document.getElementById("overall-risk-value").textContent = data.overall_risk;
            document.getElementById("axis1-value").textContent = data.axis1;
            document.getElementById("axis2-value").textContent = data.axis2;
            document.getElementById("axis3-value").textContent = data.axis3;
            document.getElementById("axis4-value").textContent = data.axis4;
            document.getElementById("axis5-value").textContent = data.axis5;
            document.getElementById("axis6-value").textContent = data.axis6;
            document.getElementById("verdict-tier-value").textContent = data.verdict_tier;
            document.getElementById("criteria-version").textContent = `Criteria Version: ${data.criteria_version}`;
            
            document.getElementById("loading").classList.add("hidden");
            document.getElementById("overall-risk").classList.remove("hidden");
            document.getElementById("risk-axes").classList.remove("hidden");
            document.getElementById("verdict-tier").classList.remove("hidden");
        }
        
        // Self-test
        function testRenderFunctions() {
            const emptyData = {
                overall_risk: "",
                axis1: "",
                axis2: "",
                axis3: "",
                axis4: "",
                axis5: "",
                axis6: "",
                verdict_tier: "",
                criteria_version: ""
            };
            
            try {
                renderData(emptyData);
                console.log("Self-test passed: render functions handle empty data");
            } catch (error) {
                console.error("Self-test failed: render functions do not handle empty data", error);
            }
        }
        
        // Run the self-test
        testRenderFunctions();
        
        // Fetch data on page load
        window.onload = fetchData;
    </script>
</body>
</html>