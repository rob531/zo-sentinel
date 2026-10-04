<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MCP Signal Scores Distribution</title>
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
            background: white;
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
        .risk-axis .score {
            font-weight: bold;
        }
        .overall {
            font-size: 1.2em;
            font-weight: bold;
            margin-top: 20px;
        }
        .verdict {
            margin-top: 20px;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            background: #e9f7ef;
        }
        .loading, .error, .empty {
            text-align: center;
            padding: 20px;
            font-size: 1.2em;
        }
    </style>
</head>
<body>
    <div class="dashboard">
        <h1>MCP Signal Scores Distribution</h1>
        <div id="loading" class="loading">Loading data...</div>
        <div id="error" class="error" style="display: none;">Error loading data. Please try again later.</div>
        <div id="empty" class="empty" style="display: none;">No data available.</div>
        <div id="content" style="display: none;">
            <div class="risk-axis">
                <h3>Risk Axis 1</h3>
                <div class="score" id="risk-axis-1-score"></div>
            </div>
            <div class="risk-axis">
                <h3>Risk Axis 2</h3>
                <div class="score" id="risk-axis-2-score"></div>
            </div>
            <div class="risk-axis">
                <h3>Risk Axis 3</h3>
                <div class="score" id="risk-axis-3-score"></div>
            </div>
            <div class="risk-axis">
                <h3>Risk Axis 4</h3>
                <div class="score" id="risk-axis-4-score"></div>
            </div>
            <div class="risk-axis">
                <h3>Risk Axis 5</h3>
                <div class="score" id="risk-axis-5-score"></div>
            </div>
            <div class="risk-axis">
                <h3>Risk Axis 6</h3>
                <div class="score" id="risk-axis-6-score"></div>
            </div>
            <div class="overall">
                <h3>Overall Score</h3>
                <div class="score" id="overall-score"></div>
            </div>
            <div class="verdict">
                <h3>Verdict</h3>
                <div id="verdict"></div>
            </div>
        </div>
    </div>
    <script>
        const API_BASE_URL = "http://localhost:8000/api";
        const authToken = "your-auth-token"; // Replace with actual token or fetch from auth state

        async function fetchData() {
            try {
                const response = await fetch(`${API_BASE_URL}/mcp_signal_scores_distribution`, {
                    headers: {
                        "Authorization": `Bearer ${authToken}`
                    }
                });

                if (!response.ok) {
                    throw new Error("Network response was not ok");
                }

                const data = await response.json();

                if (!data || Object.keys(data).length === 0) {
                    document.getElementById("loading").style.display = "none";
                    document.getElementById("empty").style.display = "block";
                } else {
                    renderData(data);
                }
            } catch (error) {
                document.getElementById("loading").style.display = "none";
                document.getElementById("error").style.display = "block";
                console.error("Error fetching data:", error);
            }
        }

        function renderData(data) {
            document.getElementById("loading").style.display = "none";
            document.getElementById("content").style.display = "block";

            document.getElementById("risk-axis-1-score").textContent = data.risk_axis_1 || "N/A";
            document.getElementById("risk-axis-2-score").textContent = data.risk_axis_2 || "N/A";
            document.getElementById("risk-axis-3-score").textContent = data.risk_axis_3 || "N/A";
            document.getElementById("risk-axis-4-score").textContent = data.risk_axis_4 || "N/A";
            document.getElementById("risk-axis-5-score").textContent = data.risk_axis_5 || "N/A";
            document.getElementById("risk-axis-6-score").textContent = data.risk_axis_6 || "N/A";
            document.getElementById("overall-score").textContent = data.overall_score || "N/A";
            document.getElementById("verdict").textContent = data.verdict || "N/A";
        }

        // Self-test
        function selfTest() {
            const testData = {
                risk_axis_1: 85,
                risk_axis_2: 75,
                risk_axis_3: 90,
                risk_axis_4: 80,
                risk_axis_5: 70,
                risk_axis_6: 95,
                overall_score: 82,
                verdict: "Tier 2"
            };
            renderData(testData);
            console.log("Self-test passed");
        }

        // Run self-test and fetch data
        document.addEventListener("DOMContentLoaded", () => {
            selfTest();
            fetchData();
        });
    </script>
</body>
</html>