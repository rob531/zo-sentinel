<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>High Risk Servers Dashboard</title>
    <style>
        /* Inline CSS */
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
        }
        .dashboard {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
        }
        .axis {
            border: 1px solid #ccc;
            padding: 10px;
            border-radius: 5px;
        }
        .tier {
            background-color: #f0f0f0;
            padding: 5px;
            margin: 5px 0;
        }
        .version {
            font-size: 12px;
            color: #666;
        }
    </style>
</head>
<body>
    <h1>High Risk Servers Dashboard</h1>
    <div id="dashboard" class="dashboard"></div>
    <div id="error" style="color: red;"></div>
    <div id="loading" style="display: none;">Loading data...</div>

    <script>
        // API base URL
        const API_BASE_URL = "/api";

        // In-memory auth state
        let authToken = "Bearer YOUR_AUTH_TOKEN";

        // Fetch data from the API
        async function fetchData() {
            const loadingDiv = document.getElementById("loading");
            const errorDiv = document.getElementById("error");

            loadingDiv.style.display = "block";
            errorDiv.textContent = "";

            try {
                const response = await fetch(`${API_BASE_URL}/high_risk_servers`, {
                    headers: {
                        "Authorization": authToken
                    }
                });

                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }

                const data = await response.json();
                renderDashboard(data);
            } catch (error) {
                errorDiv.textContent = `Error fetching data: ${error.message}`;
            } finally {
                loadingDiv.style.display = "none";
            }
        }

        // Render the dashboard
        function renderDashboard(data) {
            const dashboardDiv = document.getElementById("dashboard");
            dashboardDiv.innerHTML = "";

            // Render each risk axis
            data.axes.forEach(axis => {
                const axisDiv = document.createElement("div");
                axisDiv.className = "axis";
                axisDiv.setAttribute("aria-label", `Risk Axis: ${axis.name}`);

                const axisName = document.createElement("h2");
                axisName.textContent = axis.name;
                axisDiv.appendChild(axisName);

                // Render verdict tiers
                axis.tiers.forEach(tier => {
                    const tierDiv = document.createElement("div");
                    tierDiv.className = "tier";
                    tierDiv.setAttribute("aria-label", `Tier: ${tier.name}`);
                    tierDiv.textContent = tier.name;
                    axisDiv.appendChild(tierDiv);
                });

                dashboardDiv.appendChild(axisDiv);
            });

            // Render criteria version
            const versionDiv = document.createElement("div");
            versionDiv.className = "version";
            versionDiv.textContent = `Criteria Version: ${data.criteria_version}`;
            dashboardDiv.appendChild(versionDiv);
        }

        // Self-check block
        function runSelfCheck() {
            const mockData = {
                axes: [
                    {
                        name: "Axis 1",
                        tiers: [
                            { name: "Tier 1" },
                            { name: "Tier 2" }
                        ]
                    },
                    {
                        name: "Axis 2",
                        tiers: [
                            { name: "Tier 1" },
                            { name: "Tier 2" }
                        ]
                    }
                ],
                criteria_version: "1.0"
            };

            const dashboardDiv = document.getElementById("dashboard");
            dashboardDiv.innerHTML = "";
            renderDashboard(mockData);

            const axes = dashboardDiv.querySelectorAll(".axis");
            if (axes.length !== 2) {
                throw new Error("Self-check failed: Incorrect number of axes rendered.");
            }

            const tiers = dashboardDiv.querySelectorAll(".tier");
            if (tiers.length !== 4) {
                throw new Error("Self-check failed: Incorrect number of tiers rendered.");
            }

            const version = dashboardDiv.querySelector(".version");
            if (!version) {
                throw new Error("Self-check failed: Criteria version not rendered.");
            }

            console.log("Self-check passed.");
        }

        // Fetch data on page load
        fetchData();

        // Run self-check
        runSelfCheck();
    </script>
</body>
</html>