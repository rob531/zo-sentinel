<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Risk Tier Summary Dashboard</title>
    <style>
        /* Inline CSS */
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
            border-left: 4px solid #4CAF50;
        }
        .axis-title {
            font-weight: bold;
            margin-bottom: 10px;
        }
        .axis-value {
            font-size: 24px;
            font-weight: bold;
            color: #4CAF50;
        }
        .verdict {
            text-align: center;
            margin: 20px 0;
        }
        .verdict-tier {
            font-size: 36px;
            font-weight: bold;
            color: #2196F3;
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
        <h1>Risk Tier Summary Dashboard</h1>
        <div class="risk-axes" id="riskAxes">
            <!-- Risk axes will be populated by JavaScript -->
        </div>
        <div class="verdict">
            <div class="verdict-tier" id="verdictTier">Loading...</div>
            <div id="criteriaVersion">Criteria Version: Loading...</div>
        </div>
        <div class="loading" id="loading">Loading data...</div>
        <div class="error" id="error" style="display: none;">Error loading data</div>
        <div class="empty" id="empty" style="display: none;">No data available</div>
    </div>

    <script>
        // API configuration
        const API_BASE_URL = "http://localhost:8000/api";
        const AUTH_TOKEN = "your_auth_token_here"; // In a real app, this would come from auth state

        // DOM elements
        const riskAxesContainer = document.getElementById('riskAxes');
        const verdictTierElement = document.getElementById('verdictTier');
        const criteriaVersionElement = document.getElementById('criteriaVersion');
        const loadingElement = document.getElementById('loading');
        const errorElement = document.getElementById('error');
        const emptyElement = document.getElementById('empty');

        // Fetch data from the API
        async function fetchRiskData() {
            try {
                const response = await fetch(`${API_BASE_URL}/verdict-breakdown`, {
                    headers: {
                        'Authorization': `Bearer ${AUTH_TOKEN}`,
                        'Content-Type': 'application/json'
                    }
                });

                if (!response.ok) {
                    throw new Error('Network response was not ok');
                }

                const data = await response.json();
                renderRiskData(data);
            } catch (error) {
                console.error('Error fetching risk data:', error);
                showError();
            } finally {
                loadingElement.style.display = 'none';
            }
        }

        // Render the risk data
        function renderRiskData(data) {
            if (!data || Object.keys(data).length === 0) {
                showEmpty();
                return;
            }

            // Clear previous content
            riskAxesContainer.innerHTML = '';

            // Render risk axes
            const riskAxes = ['confidentiality', 'integrity', 'availability', 'accountability', 'safety', 'privacy'];
            riskAxes.forEach(axis => {
                const axisValue = data[axis] || 0;
                const axisElement = document.createElement('div');
                axisElement.className = 'axis';
                axisElement.innerHTML = `
                    <div class="axis-title">${axis.charAt(0).toUpperCase() + axis.slice(1)}</div>
                    <div class="axis-value" aria-label="${axis} risk score">${axisValue}</div>
                `;
                riskAxesContainer.appendChild(axisElement);
            });

            // Render verdict tier
            verdictTierElement.textContent = data.verdict_tier || 'N/A';
            criteriaVersionElement.textContent = `Criteria Version: ${data.criteria_version || 'N/A'}`;
        }

        // Show error state
        function showError() {
            errorElement.style.display = 'block';
        }

        // Show empty state
        function showEmpty() {
            emptyElement.style.display = 'block';
        }

        // Initialize the dashboard
        document.addEventListener('DOMContentLoaded', fetchRiskData);

        // Self-test verification
        function testRenderFunctions() {
            // Test with empty data
            renderRiskData({});
            console.assert(emptyElement.style.display === 'block', 'Empty state not shown');

            // Test with sample data
            const testData = {
                confidentiality: 85,
                integrity: 75,
                availability: 90,
                accountability: 80,
                safety: 70,
                privacy: 88,
                verdict_tier: 'High',
                criteria_version: '1.2.3'
            };
            renderRiskData(testData);
            console.assert(verdictTierElement.textContent === 'High', 'Verdict tier not rendered correctly');
            console.assert(criteriaVersionElement.textContent.includes('1.2.3'), 'Criteria version not rendered correctly');
        }

        // Run self-test
        testRenderFunctions();
    </script>
</body>
</html>