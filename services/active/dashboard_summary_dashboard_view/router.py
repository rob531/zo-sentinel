<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard Summary</title>
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
            text-align: center;
        }
        .card h3 {
            margin-top: 0;
        }
        .loading, .error, .empty {
            padding: 20px;
            text-align: center;
        }
    </style>
</head>
<body>
    <div class="dashboard" id="dashboard">
        <div class="loading">Loading...</div>
    </div>
    <script>
        const API_BASE_URL = "http://localhost:8000/api";
        
        async function fetchDashboardData() {
            try {
                const response = await fetch(`${API_BASE_URL}/dashboard_summary_dashboard_view`, {
                    headers: {
                        'Authorization': `Bearer ${localStorage.getItem('authToken')}`
                    }
                });
                if (!response.ok) {
                    throw new Error('Network response was not ok');
                }
                const data = await response.json();
                renderDashboard(data);
            } catch (error) {
                renderError(error.message);
            }
        }
        
        function renderDashboard(data) {
            const dashboard = document.getElementById('dashboard');
            dashboard.innerHTML = '';
            
            if (!data || data.length === 0) {
                dashboard.innerHTML = '<div class="empty">No data available</div>';
                return;
            }
            
            data.forEach(item => {
                const card = document.createElement('div');
                card.className = 'card';
                card.innerHTML = `
                    <h3>${item.title}</h3>
                    <p>${item.description}</p>
                `;
                dashboard.appendChild(card);
            });
        }
        
        function renderError(message) {
            const dashboard = document.getElementById('dashboard');
            dashboard.innerHTML = `<div class="error">Error: ${message}</div>`;
        }
        
        document.addEventListener('DOMContentLoaded', fetchDashboardData);
    </script>
</body>
</html>