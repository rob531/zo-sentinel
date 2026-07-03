// mcp_risk_tier_trend_analysis_dashboard_view.js
document.addEventListener('DOMContentLoaded', function() {
    // DOM elements
    const dateRangePicker = document.getElementById('date-range-picker');
    const orgSelect = document.getElementById('org-select');
    const riskTierSelect = document.getElementById('risk-tier-select');
    const trendChartCanvas = document.getElementById('trend-chart');
    const exportBtn = document.getElementById('export-btn');
    const loadingSpinner = document.getElementById('loading-spinner');
    const errorMessage = document.getElementById('error-message');

    // Chart instance
    let trendChart = null;

    // Initialize date range picker
    flatpickr(dateRangePicker, {
        mode: 'range',
        defaultDate: [new Date(new Date().setDate(new Date().getDate() - 30)), new Date()],
        maxDate: new Date()
    });

    // Fetch organizations for dropdown
    async function fetchOrganizations() {
        try {
            const response = await fetch('/api/organizations');
            const data = await response.json();
            orgSelect.innerHTML = '<option value="">All Organizations</option>';
            data.forEach(org => {
                const option = document.createElement('option');
                option.value = org.id;
                option.textContent = org.name;
                orgSelect.appendChild(option);
            });
        } catch (error) {
            console.error('Error fetching organizations:', error);
            showError('Failed to load organizations');
        }
    }

    // Fetch risk tier trend data
    async function fetchRiskTierTrendData() {
        const [startDate, endDate] = dateRangePicker.value.split(' to ');
        const orgId = orgSelect.value;
        const riskTier = riskTierSelect.value;

        try {
            showLoading();
            const response = await fetch(`/api/risk-tier-trends?start_date=${startDate}&end_date=${endDate}&org_id=${orgId}&risk_tier=${riskTier}`);
            const data = await response.json();

            if (trendChart) {
                trendChart.destroy();
            }

            renderTrendChart(data);
            hideLoading();
        } catch (error) {
            console.error('Error fetching risk tier trends:', error);
            showError('Failed to load risk tier trends');
            hideLoading();
        }
    }

    // Render trend chart
    function renderTrendChart(data) {
        const ctx = trendChartCanvas.getContext('2d');

        const labels = data.dates;
        const datasets = [];

        // Create dataset for each risk tier
        Object.keys(data.tiers).forEach(tier => {
            datasets.push({
                label: `Tier ${tier}`,
                data: data.tiers[tier],
                borderColor: getTierColor(tier),
                backgroundColor: getTierColor(tier, 0.2),
                borderWidth: 2,
                fill: false
            });
        });

        trendChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: datasets
            },
            options: {
                responsive: true,
                scales: {
                    x: {
                        title: {
                            display: true,
                            text: 'Date'
                        }
                    },
                    y: {
                        title: {
                            display: true,
                            text: 'Count'
                        },
                        beginAtZero: true
                    }
                },
                plugins: {
                    legend: {
                        position: 'top'
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                return `${context.dataset.label}: ${context.raw}`;
                            }
                        }
                    }
                }
            }
        });
    }

    // Helper function to get color for risk tier
    function getTierColor(tier, alpha = 1) {
        const colors = {
            '1': 'rgba(255, 99, 132, ' + alpha + ')',
            '2': 'rgba(54, 162, 235, ' + alpha + ')',
            '3': 'rgba(255, 206, 86, ' + alpha + ')',
            '4': 'rgba(75, 192, 192, ' + alpha + ')',
            '5': 'rgba(153, 102, 255, ' + alpha + ')'
        };
        return colors[tier] || 'rgba(0, 0, 0, ' + alpha + ')';
    }

    // Show loading spinner
    function showLoading() {
        loadingSpinner.style.display = 'block';
        errorMessage.style.display = 'none';
    }

    // Hide loading spinner
    function hideLoading() {
        loadingSpinner.style.display = 'none';
    }

    // Show error message
    function showError(message) {
        errorMessage.textContent = message;
        errorMessage.style.display = 'block';
    }

    // Export data to CSV
    function exportToCSV() {
        const [startDate, endDate] = dateRangePicker.value.split(' to ');
        const orgId = orgSelect.value;
        const riskTier = riskTierSelect.value;

        window.location.href = `/api/risk-tier-trends/export?start_date=${startDate}&end_date=${endDate}&org_id=${orgId}&risk_tier=${riskTier}`;
    }

    // Event listeners
    dateRangePicker.addEventListener('change', fetchRiskTierTrendData);
    orgSelect.addEventListener('change', fetchRiskTierTrendData);
    riskTierSelect.addEventListener('change', fetchRiskTierTrendData);
    exportBtn.addEventListener('click', exportToCSV);

    // Initialize
    fetchOrganizations();
    fetchRiskTierTrendData();
});