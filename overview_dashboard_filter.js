// overview_dashboard_filter.js
// Interactive filtering module for overview_dashboard_view.html

// Main function to initialize the filter module
function initializeDashboardFilter() {
    // DOM elements
    const filterForm = document.getElementById('filter-form');
    const riskTierSelect = document.getElementById('risk-tier');
    const mcpNameInput = document.getElementById('mcp-name');
    const dateFromInput = document.getElementById('date-from');
    const dateToInput = document.getElementById('date-to');
    const dashboardContent = document.getElementById('dashboard-content');
    const loadingIndicator = document.getElementById('loading-indicator');

    // Current data and filters
    let dashboardData = [];
    let currentFilters = {
        riskTier: null,
        mcpName: null,
        dateFrom: null,
        dateTo: null
    };

    // Fetch data from the API
    async function fetchDashboardData() {
        try {
            loadingIndicator.style.display = 'block';
            const response = await fetch('/api/dashboard_summary');
            if (!response.ok) {
                throw new Error('Failed to fetch dashboard data');
            }
            dashboardData = await response.json();
            renderDashboard();
        } catch (error) {
            console.error('Error fetching dashboard data:', error);
            dashboardContent.innerHTML = `<div class="error">Error loading dashboard data. Please try again later.</div>`;
        } finally {
            loadingIndicator.style.display = 'none';
        }
    }

    // Apply filters to the data
    function applyFilters(data) {
        return data.filter(item => {
            // Risk tier filter
            if (currentFilters.riskTier && item.risk_tier !== currentFilters.riskTier) {
                return false;
            }

            // MCP name filter (case-insensitive)
            if (currentFilters.mcpName && !item.mcp_name.toLowerCase().includes(currentFilters.mcpName.toLowerCase())) {
                return false;
            }

            // Date range filter
            if (currentFilters.dateFrom) {
                const itemDate = new Date(item.submission_date);
                const fromDate = new Date(currentFilters.dateFrom);
                if (itemDate < fromDate) {
                    return false;
                }
            }

            if (currentFilters.dateTo) {
                const itemDate = new Date(item.submission_date);
                const toDate = new Date(currentFilters.dateTo);
                if (itemDate > toDate) {
                    return false;
                }
            }

            return true;
        });
    }

    // Render the filtered data
    function renderDashboard() {
        const filteredData = applyFilters(dashboardData);

        if (filteredData.length === 0) {
            dashboardContent.innerHTML = `<div class="no-results">No results found matching your criteria.</div>`;
            return;
        }

        // Generate HTML for each item
        const itemsHTML = filteredData.map(item => {
            return `
                <div class="dashboard-item">
                    <h3>${item.mcp_name}</h3>
                    <div class="item-details">
                        <span class="risk-tier tier-${item.risk_tier.toLowerCase()}">${item.risk_tier}</span>
                        <span class="submission-date">${formatDate(item.submission_date)}</span>
                    </div>
                    <div class="item-content">
                        <p>${item.summary}</p>
                        <a href="/mcp/${item.mcp_id}" class="view-details">View Details</a>
                    </div>
                </div>
            `;
        }).join('');

        dashboardContent.innerHTML = itemsHTML;
    }

    // Helper function to format dates
    function formatDate(dateString) {
        const date = new Date(dateString);
        return date.toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
    }

    // Update filters based on form input
    function updateFilters() {
        currentFilters = {
            riskTier: riskTierSelect.value || null,
            mcpName: mcpNameInput.value.trim() || null,
            dateFrom: dateFromInput.value || null,
            dateTo: dateToInput.value || null
        };
        renderDashboard();
    }

    // Event listeners
    filterForm.addEventListener('input', updateFilters);
    filterForm.addEventListener('change', updateFilters);

    // Initialize
    fetchDashboardData();

    // Self-test function
    function runSelfTest() {
        console.log('Running self-test for dashboard filter module...');

        // Mock data
        const mockData = [
            {
                mcp_id: 1,
                mcp_name: 'Project Alpha',
                risk_tier: 'High',
                submission_date: '2023-05-15',
                summary: 'Summary for Project Alpha'
            },
            {
                mcp_id: 2,
                mcp_name: 'Project Beta',
                risk_tier: 'Medium',
                submission_date: '2023-06-20',
                summary: 'Summary for Project Beta'
            },
            {
                mcp_id: 3,
                mcp_name: 'Project Gamma',
                risk_tier: 'Low',
                submission_date: '2023-07-10',
                summary: 'Summary for Project Gamma'
            }
        ];

        // Test 1: No filters - should show all items
        currentFilters = { riskTier: null, mcpName: null, dateFrom: null, dateTo: null };
        const test1Result = applyFilters(mockData);
        console.assert(test1Result.length === 3, 'Test 1 failed: Should show all items with no filters');

        // Test 2: Filter by risk tier (High)
        currentFilters = { riskTier: 'High', mcpName: null, dateFrom: null, dateTo: null };
        const test2Result = applyFilters(mockData);
        console.assert(test2Result.length === 1 && test2Result[0].mcp_name === 'Project Alpha',
            'Test 2 failed: Should show only High risk items');

        // Test 3: Filter by MCP name (contains "Beta")
        currentFilters = { riskTier: null, mcpName: 'Beta', dateFrom: null, dateTo: null };
        const test3Result = applyFilters(mockData);
        console.assert(test3Result.length === 1 && test3Result[0].mcp_name === 'Project Beta',
            'Test 3 failed: Should show only items with "Beta" in name');

        // Test 4: Filter by date range (after 2023-06-01)
        currentFilters = { riskTier: null, mcpName: null, dateFrom: '2023-06-01', dateTo: null };
        const test4Result = applyFilters(mockData);
        console.assert(test4Result.length === 2,
            'Test 4 failed: Should show only items after 2023-06-01');

        // Test 5: Combined filters (Medium risk and before 2023-07-01)
        currentFilters = { riskTier: 'Medium', mcpName: null, dateFrom: null, dateTo: '2023-07-01' };
        const test5Result = applyFilters(mockData);
        console.assert(test5Result.length === 1 && test5Result[0].mcp_name === 'Project Beta',
            'Test 5 failed: Should show only Medium risk items before 2023-07-01');

        console.log('Self-test completed. Check console for any failed assertions.');
    }

    // Run self-test in development environment
    if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
        runSelfTest();
    }

    // Export for testing purposes
    return {
        fetchDashboardData,
        applyFilters,
        renderDashboard,
        runSelfTest
    };
}

// Initialize the module when DOM is loaded
document.addEventListener('DOMContentLoaded', initializeDashboardFilter);