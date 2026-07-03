document.addEventListener('DOMContentLoaded', function() {
    const ctx = document.getElementById('riskTierTrendChart').getContext('2d');
    let riskTierTrendChart = null;

    async function fetchRiskTierTrendData() {
        try {
            const response = await fetch('/dashboard/mcp-risk-tier-trend');
            if (!response.ok) {
                throw new Error('Network response was not ok');
            }
            const data = await response.json();
            return data;
        } catch (error) {
            console.error('Error fetching risk tier trend data:', error);
            return { labels: [], datasets: [] };
        }
    }

    function renderRiskTierTrendChart(data) {
        if (riskTierTrendChart) {
            riskTierTrendChart.destroy();
        }

        riskTierTrendChart = new Chart(ctx, {
            type: 'line',
            data: data,
            options: {
                responsive: true,
                plugins: {
                    legend: {
                        position: 'top',
                    },
                    title: {
                        display: true,
                        text: 'Risk Tier Trend Over Time'
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        title: {
                            display: true,
                            text: 'Number of Servers'
                        }
                    },
                    x: {
                        title: {
                            display: true,
                            text: 'Date'
                        }
                    }
                }
            }
        });
    }

    async function initializeChart() {
        const data = await fetchRiskTierTrendData();
        renderRiskTierTrendChart(data);
    }

    initializeChart();
});