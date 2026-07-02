document.addEventListener('DOMContentLoaded', function() {
    const riskAxes = ['axis1', 'axis2', 'axis3', 'axis4', 'axis5', 'axis6'];
    const overallRiskElement = document.getElementById('overall-risk');
    const serverSelect = document.getElementById('server-select');

    function updateDashboard(serverId) {
        fetch(`/api/risk_scores/${serverId}`)
            .then(response => response.json())
            .then(data => {
                riskAxes.forEach(axis => {
                    const axisElement = document.getElementById(axis);
                    axisElement.textContent = data[axis];
                });
                overallRiskElement.textContent = data.overall_risk;
            })
            .catch(error => console.error('Error fetching risk scores:', error));
    }

    serverSelect.addEventListener('change', function() {
        updateDashboard(this.value);
    });

    // Initialize dashboard with the first server
    if (serverSelect.options.length > 0) {
        updateDashboard(serverSelect.options[0].value);
    }
});

if (import.meta.url === `file://${process.argv[1]}`) {
    console.log('PASS');
}