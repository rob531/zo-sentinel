# LLM Axis Scores Distribution Dashboard

# API Base URL
API_BASE_URL = "http://localhost:8000/api"

# In-memory auth state
let authState = {
    token: "your_bearer_token_here"
};

# Fetch data from the REST API
async function fetchData() {
    try {
        const response = await fetch(`${API_BASE_URL}/llm_axis_scores_distribution`, {
            method: 'GET',
            headers: {
                'Authorization': `Bearer ${authState.token}`,
                'Content-Type': 'application/json'
            }
        });
        if (!response.ok) {
            throw new Error('Network response was not ok');
        }
        const data = await response.json();
        renderData(data);
    } catch (error) {
        console.error('Error fetching data:', error);
        document.getElementById('error').textContent = 'Error fetching data';
    }
}

# Render data to the dashboard
function renderData(data) {
    if (!data || data.length === 0) {
        document.getElementById('error').textContent = 'No data available';
        return;
    }
    
    const dashboard = document.getElementById('dashboard');
    dashboard.innerHTML = '';
    
    data.forEach(item => {
        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `
            <h3>${item.axis}</h3>
            <p>Score: ${item.score}</p>
            <p>Criteria Version: ${item.criteria_version}</p>
        `;
        dashboard.appendChild(card);
    });
}

<!-- HTML -->
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LLM Axis Scores Distribution Dashboard</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
        }
        .dashboard {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
        }
        .card {
            border: 1px solid #ccc;
            border-radius: 5px;
            padding: 15px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }
        .card h3 {
            margin-top: 0;
        }
        #error {
            color: red;
        }
    </style>
</head>
<body>
    <h1>LLM Axis Scores Distribution Dashboard</h1>
    <div id="error"></div>
    <div id="dashboard" class="dashboard"></div>
    <script>
        // Fetch data when the page loads
        document.addEventListener('DOMContentLoaded', fetchData);
    </script>
</body>
</html>

<!-- SELFTEST -->
<script>
    // Self-test: Verify render functions handle empty API response without throwing
    function testRenderData() {
        const testData = [];
        try {
            renderData(testData);
            console.log('SELFTEST: renderData passed with empty data');
        } catch (error) {
            console.error('SELFTEST: renderData failed with empty data', error);
        }
    }
    testRenderData();
</script>
