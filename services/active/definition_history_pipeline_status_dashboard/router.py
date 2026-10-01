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
        .axis {
            background-color: #f9f9f9;
            padding: 15px;
            border-radius: 5px;
            box-shadow: 0 0 5px rgba(0, 0, 0, 0.1);
        }
        .axis-title {
            font-weight: bold;
            margin-bottom: 10px;
        }
        .axis-value {
            font-size: 24px;
            font-weight: bold;
            color: #333;
        }
        .overall-risk {
            text-align: center;
            margin: 20px 0;
        }
        .verdict {
            font-size: 20px;
            font-weight: bold;
            margin-top: 10px;
        }
        .loading, .error, .empty {
            text-align: center;
            padding: 20px;
            font-size: 18px;
        }
        .criteria-version {
            text-align: right;
            font-size: 12px;
            color: #666;
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="dashboard">
        <div class="header">
            <h1>Definition History Pipeline Status Dashboard</h1>
        </div>
        <div id="loading" class="loading">Loading data...</div>
        <div id="error" class="error" style="display: none;">Error loading data</div>
        <div id="empty" class="empty" style="display: none;">No data available</div>
        <div id="content" style="display: none;">
            <div class="risk-axes">
                <div class="axis">
                    <div class="axis-title">Risk Axis 1</div>
                    <div id="axis1" class="axis-value">0</div>
                </div>
                <div class="axis">
                    <div class="axis-title">Risk Axis 2</div>
                    <div id="axis2" class="axis-value">0</div>
                </div>
                <div class="axis">
                    <div class="axis-title">Risk Axis 3</div>
                    <div id="axis3" class="axis-value">0</div>
                </div>
                <div class="axis">
                    <div class="axis-title">Risk Axis 4</div>
                    <div id="axis4" class="axis-value">0</div>
                </div>
                <div class="axis">
                    <div class="axis-title">Risk Axis 5</div>
                    <div id="axis5" class="axis-value">0</div>
                </div>
                <div class="axis">
                    <div class="axis-title">Risk Axis 6</div>
                    <div id="axis6" class="axis-value">0</div>
                </div>
            </div>
            <div class="overall-risk">
                <div>Overall Risk</div>
                <div id="overall" class="axis-value">0</div>
                <div id="verdict" class="verdict">N/A</div>
            </div>
            <div class="criteria-version">
                Criteria Version: <span id="version">N/A</span>
            </div>
        </div>
    </div>

    <script>
        const API_BASE_URL = "http://localhost:8000/api";
        let authToken = ""; // This would be set from in-memory auth state

        async function fetchData() {
            try {
                const response = await fetch(`${API_BASE_URL}/definition-history-pipeline-status`, {
                    headers: {
                        'Authorization': `Bearer ${authToken}`
                    }
                });

                if (!response.ok) {
                    throw new Error('Network response was not ok');
                }

                const data = await response.json();

                if (!data || Object.keys(data).length === 0) {
                    document.getElementById('loading').style.display = 'none';
                    document.getElementById('empty').style.display = 'block';
                    return;
                }

                updateDashboard(data);
            } catch (error) {
                console.error('Error fetching data:', error);
                document.getElementById('loading').style.display = 'none';
                document.getElementById('error').style.display = 'block';
            }
        }

        function updateDashboard(data) {
            document.getElementById('axis1').textContent = data.axis1 || 0;
            document.getElementById('axis2').textContent = data.axis2 || 0;
            document.getElementById('axis3').textContent = data.axis3 || 0;
            document.getElementById('axis4').textContent = data.axis4 || 0;
            document.getElementById('axis5').textContent = data.axis5 || 0;
            document.getElementById('axis6').textContent = data.axis6 || 0;
            document.getElementById('overall').textContent = data.overall || 0;
            document.getElementById('verdict').textContent = data.verdict || 'N/A';
            document.getElementById('version').textContent = data.version || 'N/A';

            document.getElementById('loading').style.display = 'none';
            document.getElementById('content').style.display = 'block';
        }

        // Self-test block
        function testRenderFunctions() {
            const testData = {
                axis1: 10,
                axis2: 20,
                axis3: 30,
                axis4: 40,
                axis5: 50,
                axis6: 60,
                overall: 70,
                verdict: 'High Risk',
                version: '1.0'
            };

            updateDashboard(testData);

            // Verify the UI was updated correctly
            if (
                document.getElementById('axis1').textContent !== '10' ||
                document.getElementById('axis2').textContent !== '20' ||
                document.getElementById('axis3').textContent !== '30' ||
                document.getElementById('axis4').textContent !== '40' ||
                document.getElementById('axis5').textContent !== '50' ||
                document.getElementById('axis6').textContent !== '60' ||
                document.getElementById('overall').textContent !== '70' ||
                document.getElementById('verdict').textContent !== 'High Risk' ||
                document.getElementById('version').textContent !== '1.0'
            ) {
                throw new Error('Render functions failed to update UI correctly');
            }

            console.log('Self-test passed');
        }

        // Run the self-test when the page loads
        window.onload = function() {
            testRenderFunctions();
            fetchData();
        };
    </script>
</body>
</html>