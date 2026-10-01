<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Vulnerability Analysis Dashboard</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }
        .dashboard {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            max-width: 1200px;
            margin: 0 auto;
        }
        .card {
            background: white;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .risk-axis {
            display: flex;
            align-items: center;
            margin-bottom: 10px;
        }
        .risk-axis .label {
            width: 150px;
            text-align: right;
            margin-right: 10px;
        }
        .risk-axis .value {
            flex-grow: 1;
        }
        .risk-axis .bar {
            height: 20px;
            background-color: #e0e0e0;
            border-radius: 4px;
            margin-left: 10px;
            overflow: hidden;
        }
        .risk-axis .bar .fill {
            height: 100%;
            background-color: #4CAF50;
            transition: width 0.3s;
        }
        .verdict {
            font-size: 1.5em;
            font-weight: bold;
            text-align: center;
            margin: 20px 0;
        }
        .verdict-tier {
            display: inline-block;
            padding: 5px 10px;
            border-radius: 4px;
            margin-left: 10px;
        }
        .verdict-tier-high {
            background-color: #f44336;
            color: white;
        }
        .verdict-tier-medium {
            background-color: #ff9800;
            color: white;
        }
        .verdict-tier-low {
            background-color: #4CAF50;
            color: white;
        }
        .verdict-tier-none {
            background-color: #9E9E9E;
            color: white;
        }
        .criteria-version {
            font-size: 0.8em;
            color: #666;
            text-align: center;
        }
        .loading {
            text-align: center;
            margin: 50px 0;
        }
        .error {
            color: red;
            text-align: center;
            margin: 20px 0;
        }
        .empty {
            text-align: center;
            margin: 20px 0;
            color: #666;
        }
        button {
            background-color: #4CAF50;
            color: white;
            padding: 10px 15px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            margin-top: 20px;
        }
        button:hover {
            background-color: #45a049;
        }
        button:focus {
            outline: none;
            box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.5);
        }
        .refresh-button {
            margin: 20px auto;
            display: block;
        }
    </style>
</head>
<body>
    <h1>Vulnerability Analysis Dashboard</h1>
    
    <div class="loading" id="loading">Loading data...</div>
    <div class="error" id="error" style="display: none"></div>
    <div class="empty" id="empty" style="display: none">No data available for the selected criteria.</div>
    
    <div class="dashboard" id="dashboard" style="display: none">
        <div class="card">
            <h2>Risk Profile</h2>
            <div id="risk-profile"></div>
            <div class="verdict">
                Overall Verdict: <span id="overall-verdict"></span>
                <span id="verdict-tier" class="verdict-tier"></span>
            </div>
            <div class="criteria-version" id="criteria-version"></div>
        </div>
    </div>
    
    <button class="refresh-button" id="refresh-button">Refresh Data</button>
    
    <script>
        const API_BASE_URL = '/api/vuln_analysis';
        let authToken = null;
        
        // Mock auth token for demonstration - in real app this would come from login flow
        authToken = 'mock-bearer-token-12345';
        
        async function fetchDashboardData() {
            const loadingElement = document.getElementById('loading');
            const errorElement = document.getElementById('error');
            const emptyElement = document.getElementById('empty');
            const dashboardElement = document.getElementById('dashboard');
            
            // Show loading state
            loadingElement.style.display = 'block';
            errorElement.style.display = 'none';
            emptyElement.style.display = 'none';
            dashboardElement.style.display = 'none';
            
            try {
                const response = await fetch(API_BASE_URL, {
                    method: 'GET',
                    headers: {
                        'Authorization': `Bearer ${authToken}`,
                        'Content-Type': 'application/json'
                    }
                });
                
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                
                const data = await response.json();
                
                if (!data || Object.keys(data).length === 0) {
                    // Show empty state
                    loadingElement.style.display = 'none';
                    emptyElement.style.display = 'block';
                    return;
                }
                
                // Process and display data
                displayDashboardData(data);
            } catch (error) {
                // Show error state
                loadingElement.style.display = 'none';
                errorElement.textContent = `Error loading data: ${error.message}`;
                errorElement.style.display = 'block';
                console.error('Error fetching dashboard data:', error);
            }
        }
        
        function displayDashboardData(data) {
            const dashboardElement = document.getElementById('dashboard');
            const riskProfileElement = document.getElementById('risk-profile');
            const overallVerdictElement = document.getElementById('overall-verdict');
            const verdictTierElement = document.getElementById('verdict-tier');
            const criteriaVersionElement = document.getElementById('criteria-version');
            
            // Hide loading and error states
            document.getElementById('loading').style.display = 'none';
            document.getElementById('error').style.display = 'none';
            document.getElementById('empty').style.display = 'none';
            
            // Show dashboard
            dashboardElement.style.display = 'grid';
            
            // Clear previous content
            riskProfileElement.innerHTML = '';
            
            // Display risk profile
            const riskAxes = [
                { label: 'Network Security', value: data.network_security || 0 },
                { label: 'Application Security', value: data.application_security || 0 },
                { label: 'Operating System', value: data.operating_system || 0 },
                { label: 'Configuration', value: data.configuration || 0 },
                { label: 'Patch Management', value: data.patch_management || 0 },
                { label: 'Access Control', value: data.access_control || 0 }
            ];
            
            riskAxes.forEach(axis => {
                const riskAxisElement = document.createElement('div');
                riskAxisElement.className = 'risk-axis';
                
                const labelElement = document.createElement('div');
                labelElement.className = 'label';
                labelElement.textContent = axis.label;
                
                const valueElement = document.createElement('div');
                valueElement.className = 'value';
                valueElement.textContent = axis.value;
                
                const barElement = document.createElement('div');
                barElement.className = 'bar';
                
                const fillElement = document.createElement('div');
                fillElement.className = 'fill';
                fillElement.style.width = `${axis.value}%`;
                
                barElement.appendChild(fillElement);
                
                riskAxisElement.appendChild(labelElement);
                riskAxisElement.appendChild(valueElement);
                riskAxisElement.appendChild(barElement);
                
                riskProfileElement.appendChild(riskAxisElement);
            });
            
            // Display overall verdict
            overallVerdictElement.textContent = data.overall_verdict || 'N/A';
            
            // Display verdict tier
            const verdictTier = data.verdict_tier || 'none';
            verdictTierElement.textContent = verdictTier;
            verdictTierElement.className = `verdict-tier verdict-tier-${verdictTier}`;
            
            // Display criteria version
            criteriaVersionElement.textContent = `Criteria Version: ${data.criteria_version || 'unknown'}`;
        }
        
        // Event listeners
        document.getElementById('refresh-button').addEventListener('click', fetchDashboardData);
        
        // Initial data fetch
        fetchDashboardData();
        
        // SELFTEST
        // This section verifies the render functions handle empty API responses
        function runSelfTest() {
            // Test empty response handling
            const emptyData = {};
            
            // Mock DOM elements for testing
            const testDashboardElement = document.createElement('div');
            testDashboardElement.id = 'dashboard';
            
            const testRiskProfileElement = document.createElement('div');
            testRiskProfileElement.id = 'risk-profile';
            
            const testOverallVerdictElement = document.createElement('div');
            testOverallVerdictElement.id = 'overall-verdict';
            
            const testVerdictTierElement = document.createElement('div');
            testVerdictTierElement.id = 'verdict-tier';
            
            const testCriteriaVersionElement = document.createElement('div');
            testCriteriaVersionElement.id = 'criteria-version';
            
            // Append to body for testing
            document.body.appendChild(testDashboardElement);
            testDashboardElement.appendChild(testRiskProfileElement);
            testDashboardElement.appendChild(testOverallVerdictElement);
            testDashboardElement.appendChild(testVerdictTierElement);
            testDashboardElement.appendChild(testCriteriaVersionElement);
            
            // Run test
            displayDashboardData(emptyData);
            
            // Verify results
            const testPassed = 
                testDashboardElement.style.display === 'grid' &&
                testRiskProfileElement.children.length === 0 &&
                testOverallVerdictElement.textContent === 'N/A' &&
                testVerdictTierElement.textContent === 'none' &&
                testCriteriaVersionElement.textContent.includes('Criteria Version: unknown');
            
            // Clean up
            document.body.removeChild(testDashboardElement);
            
            // Report result
            if (testPassed) {
                console.log('SELFTEST PASSED: Empty response handling works correctly');
            } else {
                console.error('SELFTEST FAILED: Empty response handling issues detected');
            }
        }
        
        // Run self-test on page load
        runSelfTest();
    </script>
</body>
</html>