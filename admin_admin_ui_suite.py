# admin_admin_ui_suite.py
import json
from flask import Flask, render_template, request, redirect, url_for, jsonify
import requests

# Assume registry_api is running on port 8781
REGISTRY_API_URL = "http://localhost:8781/registry"

app = Flask(__name__)

# --- Helper Functions ---

def call_registry_api(method, endpoint, data=None):
    """Helper to call the registry API."""
    url = f"{REGISTRY_API_URL}/{endpoint}"
    try:
        if method.upper() == 'GET':
            response = requests.get(url)
        elif method.upper() == 'POST':
            response = requests.post(url, json=data)
        elif method.upper() == 'DELETE':
            response = requests.delete(url, json=data)
        elif method.upper() == 'PUT':
            response = requests.put(url, json=data)
        else:
            return None, "Unsupported HTTP method"

        response.raise_for_status()  # Raise an exception for bad status codes
        return response.json(), None
    except requests.exceptions.RequestException as e:
        return None, str(e)
    except json.JSONDecodeError:
        return None, "Failed to decode JSON response from registry API"

# --- HTML Templates (as strings for simplicity, in a real app these would be separate files) ---

SENTINEL_STATUS_HTML = """
<div class="sentinel-status">
    <h2>Sentinel Status</h2>
    <p>System is operational.</p>
    <!-- More status indicators can be added here -->
</div>
"""

ADMIN_EXEMPTIONS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>MCP Exemptions Management</title>
    <style>
        body { font-family: sans-serif; }
        table { border-collapse: collapse; width: 80%; margin: 20px auto; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        .form-container { margin: 20px auto; padding: 20px; border: 1px solid #ddd; width: 50%; }
        .form-container input, .form-container select, .form-container button { margin-bottom: 10px; padding: 8px; width: calc(100% - 16px); }
    </style>
</head>
<body>
    <h1>MCP Exemptions Management</h1>
    {{ sentinel_status_html | safe }}

    <div class="form-container">
        <h2>Add/Edit Exemption</h2>
        <form id="exemption-form">
            <input type="hidden" id="exemption-id">
            <input type="text" id="subject" placeholder="Subject (e.g., user ID, service name)" required><br>
            <select id="exemption-type" required>
                <option value="">Select Type</option>
                <option value="user">User</option>
                <option value="service">Service</option>
                <option value="group">Group</option>
            </select><br>
            <input type="datetime-local" id="valid-until" required><br>
            <button type="submit">Save Exemption</button>
            <button type="button" onclick="clearForm()">Clear</button>
        </form>
    </div>

    <h2>Existing Exemptions</h2>
    <table id="exemptions-table">
        <thead>
            <tr>
                <th>ID</th>
                <th>Subject</th>
                <th>Type</th>
                <th>Valid Until</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
            <!-- Exemptions will be loaded here -->
        </tbody>
    </table>

    <script>
        const exemptionsTableBody = document.getElementById('exemptions-table').getElementsByTagName('tbody')[0];
        const exemptionForm = document.getElementById('exemption-form');
        const exemptionIdInput = document.getElementById('exemption-id');
        const subjectInput = document.getElementById('subject');
        const typeInput = document.getElementById('exemption-type');
        const validUntilInput = document.getElementById('valid-until');

        function fetchExemptions() {
            fetch('/admin/exemptions/api')
                .then(response => response.json())
                .then(data => {
                    exemptionsTableBody.innerHTML = ''; // Clear existing rows
                    data.forEach(exemption => {
                        const row = exemptionsTableBody.insertRow();
                        row.innerHTML = `
                            <td>${exemption.id}</td>
                            <td>${exemption.subject}</td>
                            <td>${exemption.exemption_type}</td>
                            <td>${new Date(exemption.valid_until).toLocaleString()}</td>
                            <td>
                                <button onclick="editExemption(${JSON.stringify(exemption)})">Edit</button>
                                <button onclick="deleteExemption(${exemption.id})">Delete</button>
                            </td>
                        `;
                    });
                })
                .catch(error => console.error('Error fetching exemptions:', error));
        }

        function submitExemption(event) {
            event.preventDefault();
            const id = exemptionIdInput.value;
            const subject = subjectInput.value;
            const exemption_type = typeInput.value;
            const valid_until = validUntilInput.value;

            const exemptionData = { subject, exemption_type, valid_until };

            let url = '/admin/exemptions/api';
            let method = 'POST';
            if (id) {
                url += `/${id}`;
                method = 'PUT';
                exemptionData.id = parseInt(id); // Ensure ID is sent if editing
            }

            fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(exemptionData)
            })
            .then(response => {
                if (!response.ok) throw new Error('Network response was not ok');
                return response.json();
            })
            .then(() => {
                clearForm();
                fetchExemptions();
            })
            .catch(error => console.error('Error saving exemption:', error));
        }

        function editExemption(exemption) {
            exemptionIdInput.value = exemption.id;
            subjectInput.value = exemption.subject;
            typeInput.value = exemption.exemption_type;
            // Format datetime-local input correctly
            const validUntilDate = new Date(exemption.valid_until);
            const offset = validUntilDate.getTimezoneOffset() * 60000; // offset in milliseconds
            const localValidUntil = new Date(validUntilDate.getTime() - offset).toISOString().slice(0, 16);
            validUntilInput.value = localValidUntil;
        }

        function deleteExemption(id) {
            if (!confirm('Are you sure you want to delete this exemption?')) return;

            fetch(`/admin/exemptions/api/${id}`, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' }
            })
            .then(response => {
                if (!response.ok) throw new Error('Network response was not ok');
                fetchExemptions();
            })
            .catch(error => console.error('Error deleting exemption:', error));
        }

        function clearForm() {
            exemptionForm.reset();
            exemptionIdInput.value = '';
        }

        exemptionForm.addEventListener('submit', submitExemption);
        document.addEventListener('DOMContentLoaded', fetchExemptions);
    </script>
</body>
</html>
"""

ADMIN_POLICIES_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>MCP Policy Rules Management</title>
    <style>
        body { font-family: sans-serif; }
        table { border-collapse: collapse; width: 80%; margin: 20px auto; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        .form-container { margin: 20px auto; padding: 20px; border: 1px solid #ddd; width: 50%; }
        .form-container input, .form-container select, .form-container button { margin-bottom: 10px; padding: 8px; width: calc(100% - 16px); }
    </style>
</head>
<body>
    <h1>MCP Policy Rules Management</h1>
    {{ sentinel_status_html | safe }}

    <div class="form-container">
        <h2>Add/Edit Policy Rule</h2>
        <form id="policy-form">
            <input type="hidden" id="policy-id">
            <input type="text" id="rule-name" placeholder="Rule Name (e.g., 'Allow SSH')" required><br>
            <select id="rule-type" required>
                <option value="">Select Type</option>
                <option value="allow">Allow</option>
                <option value="deny">Deny</option>
            </select><br>
            <input type="text" id="pattern" placeholder="Pattern (e.g., 'service:ssh', 'user:*')" required><br>
            <button type="submit">Save Policy Rule</button>
            <button type="button" onclick="clearForm()">Clear</button>
        </form>
    </div>

    <h2>Existing Policy Rules</h2>
    <table id="policies-table">
        <thead>
            <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Type</th>
                <th>Pattern</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
            <!-- Policy rules will be loaded here -->
        </tbody>
    </table>

    <script>
        const policiesTableBody = document.getElementById('policies-table').getElementsByTagName('tbody')[0];
        const policyForm = document.getElementById('policy-form');
        const policyIdInput = document.getElementById('policy-id');
        const ruleNameInput = document.getElementById('rule-name');
        const ruleTypeInput = document.getElementById('rule-type');
        const patternInput = document.getElementById('pattern');

        function fetchPolicies() {
            fetch('/admin/policies/api')
                .then(response => response.json())
                .then(data => {
                    policiesTableBody.innerHTML = ''; // Clear existing rows
                    data.forEach(policy => {
                        const row = policiesTableBody.insertRow();
                        row.innerHTML = `
                            <td>${policy.id}</td>
                            <td>${policy.rule_name}</td>
                            <td>${policy.rule_type}</td>
                            <td>${policy.pattern}</td>
                            <td>
                                <button onclick="editPolicy(${JSON.stringify(policy)})">Edit</button>
                                <button onclick="deletePolicy(${policy.id})">Delete</button>
                            </td>
                        `;
                    });
                })
                .catch(error => console.error('Error fetching policies:', error));
        }

        function submitPolicy(event) {
            event.preventDefault();
            const id = policyIdInput.value;
            const rule_name = ruleNameInput.value;
            const rule_type = ruleTypeInput.value;
            const pattern = patternInput.value;

            const policyData = { rule_name, rule_type, pattern };

            let url = '/admin/policies/api';
            let method = 'POST';
            if (id) {
                url += `/${id}`;
                method = 'PUT';
                policyData.id = parseInt(id); // Ensure ID is sent if editing
            }

            fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(policyData)
            })
            .then(response => {
                if (!response.ok) throw new Error('Network response was not ok');
                return response.json();
            })
            .then(() => {
                clearForm();
                fetchPolicies();
            })
            .catch(error => console.error('Error saving policy:', error));
        }

        function editPolicy(policy) {
            policyIdInput.value = policy.id;
            ruleNameInput.value = policy.rule_name;
            ruleTypeInput.value = policy.rule_type;
            patternInput.value = policy.pattern;
        }

        function deletePolicy(id) {
            if (!confirm('Are you sure you want to delete this policy rule?')) return;

            fetch(`/admin/policies/api/${id}`, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' }
            })
            .then(response => {
                if (!response.ok) throw new Error('Network response was not ok');
                fetchPolicies();
            })
            .catch(error => console.error('Error deleting policy:', error));
        }

        function clearForm() {
            policyForm.reset();
            policyIdInput.value = '';
        }

        policyForm.addEventListener('submit', submitPolicy);
        document.addEventListener('DOMContentLoaded', fetchPolicies);
    </script>
</body>
</html>
"""

ADMIN_SUBMISSIONS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>MCP Submissions Triage</title>
    <style>
        body { font-family: sans-serif; }
        table { border-collapse: collapse; width: 90%; margin: 20px auto; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        .status-pending { color: orange; font-weight: bold; }
        .status-approved { color: green; }
        .status-rejected { color: red; }
        .action-button { padding: 5px 10px; margin: 0 5px; cursor: pointer; }
        .triage-container { margin: 20px auto; padding: 20px; border: 1px solid #ddd; width: 80%; }
    </style>
</head>
<body>
    <h1>MCP Submissions Triage</h1>
    {{ sentinel_status_html | safe }}

    <div class="triage-container">
        <h2>Pending Submissions</h2>
        <table id="submissions-table">
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Subject</th>
                    <th>Timestamp</th>
                    <th>Status</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                <!-- Submissions will be loaded here -->
            </tbody>
        </table>
    </div>

    <script>
        const submissionsTableBody = document.getElementById('submissions-table').getElementsByTagName('tbody')[0];

        function fetchSubmissions() {
            fetch('/admin/submissions/api?status=pending')
                .then(response => response.json())
                .then(data => {
                    submissionsTableBody.innerHTML = ''; // Clear existing rows
                    data.forEach(submission => {
                        const row = submissionsTableBody.insertRow();
                        let statusClass = 'status-pending';
                        if (submission.status === 'approved') statusClass = 'status-approved';
                        if (submission.status === 'rejected') statusClass = 'status-rejected';

                        row.innerHTML = `
                            <td>${submission.id}</td>
                            <td>${submission.subject}</td>
                            <td>${new Date(submission.timestamp).toLocaleString()}</td>
                            <td class="${statusClass}">${submission.status.toUpperCase()}</td>
                            <td>
                                <button class="action-button" onclick="triageSubmission(${submission.id}, 'approved')">Approve</button>
                                <button class="action-button" onclick="triageSubmission(${submission.id}, 'rejected')">Reject</button>
                            </td>
                        `;
                    });
                })
                .catch(error => console.error('Error fetching submissions:', error));
        }

        function triageSubmission(id, action) {
            if (!confirm(`Are you sure you want to ${action} this submission?`)) return;

            fetch(`/admin/submissions/api/${id}`, {
                method: 'PUT', // Assuming PUT for updating status
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: action })
            })
            .then(response => {
                if (!response.ok) throw new Error('Network response was not ok');
                return response.json();
            })
            .then(() => {
                fetchSubmissions(); // Refresh the list
            })
            .catch(error => console.error('Error triaging submission:', error));
        }

        document.addEventListener('DOMContentLoaded', fetchSubmissions);
    </script>
</body>
</html>
"""

ADMIN_ATTESTATIONS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>MCP Attestations Management</title>
    <style>
        body { font-family: sans-serif; }
        table { border-collapse: collapse; width: 80%; margin: 20px auto; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        .form-container { margin: 20px auto; padding: 20px; border: 1px solid #ddd; width: 50%; }
        .form-container input, .form-container button { margin-bottom: 10px; padding: 8px; width: calc(100% - 16px); }
    </style>
</head>
<body>
    <h1>MCP Attestations Management</h1>
    {{ sentinel_status_html | safe }}

    <div class="form-container">
        <h2>Revoke/Extend Attestation</h2>
        <form id="attestation-form">
            <input type="text" id="attestation-id" placeholder="Attestation ID to modify" required><br>
            <label for="action">Action:</label>
            <select id="action" required>
                <option value="">Select Action</option>
                <option value="revoke">Revoke</option>
                <option value="extend">Extend</option>
            </select><br>
            <input type="datetime-local" id="new-valid-until" placeholder="New Valid Until (for extend)">
            <button type="submit">Submit Action</button>
            <button type="button" onclick="clearForm()">Clear</button>
        </form>
    </div>

    <h2>Existing Attestations (Sample - Full list might be too long)</h2>
    <p><em>Note: This view shows a sample. Use the form above to manage specific attestations by ID.</em></p>
    <table id="attestations-table">
        <thead>
            <tr>
                <th>ID</th>
                <th>Subject</th>
                <th>Valid Until</th>
                <th>Status</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
            <!-- Attestations will be loaded here -->
        </tbody>
    </table>

    <script>
        const attestationsTableBody = document.getElementById('attestations-table').getElementsByTagName('tbody')[0];
        const attestationForm = document.getElementById('attestation-form');
        const attestationIdInput = document.getElementById('attestation-id');
        const actionInput = document.getElementById('action');
        const newValidUntilInput = document.getElementById('new-valid-until');

        function fetchAttestations() {
            // Fetching a limited number for display purposes
            fetch('/admin/attestations/api?limit=5')
                .then(response => response.json())
                .then(data => {
                    attestationsTableBody.innerHTML = ''; // Clear existing rows
                    data.forEach(attestation => {
                        const row = attestationsTableBody.insertRow();
                        row.innerHTML = `
                            <td>${attestation.id}</td>
                            <td>${attestation.subject}</td>
                            <td>${new Date(attestation.valid_until).toLocaleString()}</td>
                            <td>${attestation.status}</td>
                            <td>
                                <button onclick="prefillForm(${JSON.stringify(attestation)})">Modify</button>
                            </td>
                        `;
                    });
                })
                .catch(error => console.error('Error fetching attestations:', error));
        }

        function submitAttestationAction(event) {
            event.preventDefault();
            const id = attestationIdInput.value;
            const action = actionInput.value;
            const new_valid_until = newValidUntilInput.value;

            if (!id || !action) {
                alert('Please provide Attestation ID and select an Action.');
                return;
            }

            let url = `/admin/attestations/api/${id}`;
            let method = 'POST'; // Use POST for actions like revoke/extend
            let payload = { action: action };

            if (action === 'extend') {
                if (!new_valid_until) {
                    alert('Please provide a new "Valid Until" date for extension.');
                    return;
                }
                payload.valid_until = new_valid_until;
            } else if (action === 'revoke') {
                // No additional data needed for revoke, but we could add a reason if the API supported it
            } else {
                alert('Invalid action selected.');
                return;
            }

            fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            })
            .then(response => {
                if (!response.ok) {
                    // Try to get error message from response body
                    return response.json().then(err => { throw new Error(err.error || `HTTP error ${response.status}`) });
                }
                return response.json();
            })
            .then((data) => {
                alert(`Attestation ${action} successful: ${data.message || ''}`);
                clearForm();
                fetchAttestations(); // Refresh the list
            })
            .catch(error => {
                console.error('Error performing attestation action:', error);
                alert(`Error: ${error.message}`);
            });
        }

        function prefillForm(attestation) {
            attestationIdInput.value = attestation.id;
            // Set action to 'extend' by default if valid_until is in the past or soon
            const validUntilDate = new Date(attestation.valid_until);
            const now = new Date();
            if (validUntilDate < now) {
                 actionInput.value = 'extend';
                 newValidUntilInput.style.display = 'block'; // Show extend field
            } else {
                 actionInput.value = 'revoke'; // Default to revoke if active
                 newValidUntilInput.style.display = 'none'; // Hide extend field
            }
            // Format datetime-local input correctly for extension
            const offset = validUntilDate.getTimezoneOffset() * 60000; // offset in milliseconds
            const localValidUntil = new Date(validUntilDate.getTime() - offset).toISOString().slice(0, 16);
            newValidUntilInput.value = localValidUntil;
        }

        function clearForm() {
            attestationForm.reset();
            attestationIdInput.value = '';
            newValidUntilInput.style.display = 'block'; // Reset visibility
        }

        // Toggle visibility of new-valid-until based on action selection
        actionInput.addEventListener('change', () => {
            if (actionInput.value === 'extend') {
                newValidUntilInput.style.display = 'block';
            } else {
                newValidUntilInput.style.display = 'none';
            }
        });

        attestationForm.addEventListener('submit', submitAttestationAction);
        document.addEventListener('DOMContentLoaded', fetchAttestations);
    </script>
</body>
</html>
"""

# --- Flask Routes ---

@app.route('/admin/exemptions')
def admin_exemptions():
    return render_template_string(ADMIN_EXEMPTIONS_HTML, sentinel_status_html=SENTINEL_STATUS_HTML)

@app.route('/admin/exemptions/api', methods=['GET'])
def api_get_exemptions():
    data, error = call_registry_api('GET', 'mcp-exemption')
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data)

@app.route('/admin/exemptions/api', methods=['POST'])
def api_create_exemption():
    exemption_data = request.get_json()
    if not exemption_data:
        return jsonify({"error": "Invalid JSON payload"}), 400
    data, error = call_registry_api('POST', 'mcp-exemption', data=exemption_data)
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data), 201

@app.route('/admin/exemptions/api/<int:exemption_id>', methods=['GET'])
def api_get_exemption_by_id(exemption_id):
    data, error = call_registry_api('GET', f'mcp-exemption/{exemption_id}')
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data)

@app.route('/admin/exemptions/api/<int:exemption_id>', methods=['PUT'])
def api_update_exemption(exemption_id):
    exemption_data = request.get_json()
    if not exemption_data:
        return jsonify({"error": "Invalid JSON payload"}), 400
    # Ensure the ID from the URL is used, not one potentially in the payload
    exemption_data['id'] = exemption_id
    data, error = call_registry_api('PUT', f'mcp-exemption/{exemption_id}', data=exemption_data)
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data)

@app.route('/admin/exemptions/api/<int:exemption_id>', methods=['DELETE'])
def api_delete_exemption(exemption_id):
    data, error = call_registry_api('DELETE', f'mcp-exemption/{exemption_id}')
    if error:
        return jsonify({"error": error}), 500
    # Registry API might return the deleted item or a success message
    return jsonify({"message": f"Exemption {exemption_id} deleted successfully", "result": data})


@app.route('/admin/policies')
def admin_policies():
    return render_template_string(ADMIN_POLICIES_HTML, sentinel_status_html=SENTINEL_STATUS_HTML)

@app.route('/admin/policies/api', methods=['GET'])
def api_get_policies():
    data, error = call_registry_api('GET', 'mcp-policy-rule')
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data)

@app.route('/admin/policies/api', methods=['POST'])
def api_create_policy():
    policy_data = request.get_json()
    if not policy_data:
        return jsonify({"error": "Invalid JSON payload"}), 400
    data, error = call_registry_api('POST', 'mcp-policy-rule', data=policy_data)
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data), 201

@app.route('/admin/policies/api/<int:policy_id>', methods=['GET'])
def api_get_policy_by_id(policy_id):
    data, error = call_registry_api('GET', f'mcp-policy-rule/{policy_id}')
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data)

@app.route('/admin/policies/api/<int:policy_id>', methods=['PUT'])
def api_update_policy(policy_id):
    policy_data = request.get_json()
    if not policy_data:
        return jsonify({"error": "Invalid JSON payload"}), 400
    policy_data['id'] = policy_id
    data, error = call_registry_api('PUT', f'mcp-policy-rule/{policy_id}', data=policy_data)
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data)

@app.route('/admin/policies/api/<int:policy_id>', methods=['DELETE'])
def api_delete_policy(policy_id):
    data, error = call_registry_api('DELETE', f'mcp-policy-rule/{policy_id}')
    if error:
        return jsonify({"error": error}), 500
    return jsonify({"message": f"Policy rule {policy_id} deleted successfully", "result": data})


@app.route('/admin/submissions')
def admin_submissions():
    return render_template_string(ADMIN_SUBMISSIONS_HTML, sentinel_status_html=SENTINEL_STATUS_HTML)

@app.route('/admin/submissions/api', methods=['GET'])
def api_get_submissions():
    status_filter = request.args.get('status')
    endpoint = 'mcp-submission'
    if status_filter:
        endpoint += f'?status={status_filter}'

    data, error = call_registry_api('GET', endpoint)
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data)

@app.route('/admin/submissions/api/<int:submission_id>', methods=['GET'])
def api_get_submission_by_id(submission_id):
    data, error = call_registry_api('GET', f'mcp-submission/{submission_id}')
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data)

@app.route('/admin/submissions/api/<int:submission_id>', methods=['PUT'])
def api_update_submission_status(submission_id):
    update_data = request.get_json()
    if not update_data or 'status' not in update_data:
        return jsonify({"error": "Invalid JSON payload, 'status' field is required"}), 400

    # The registry API likely expects a specific endpoint or payload for status updates.
    # Assuming a PUT to the specific submission ID with the status in the body.
    data, error = call_registry_api('PUT', f'mcp-submission/{submission_id}', data=update_data)
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data)

# DELETE endpoint for submissions might exist but is less common for triage actions.
# If needed, it would look like this:
# @app.route('/admin/submissions/api/<int:submission_id>', methods=['DELETE'])
# def api_delete_submission(submission_id):
#     data, error = call_registry_api('DELETE', f'mcp-submission/{submission_id}')
#     if error:
#         return jsonify({"error": error}), 500
#     return jsonify({"message": f"Submission {submission_id} deleted successfully", "result": data})


@app.route('/admin/attestations')
def admin_attestations():
    return render_template_string(ADMIN_ATTESTATIONS_HTML, sentinel_status_html=SENTINEL_STATUS_HTML)

@app.route('/admin/attestations/api', methods=['GET'])
def api_get_attestations():
    # Allow optional limit parameter for the sample view
    limit = request.args.get('limit')
    endpoint = 'mcp-attestation'
    if limit:
        endpoint += f'?limit={limit}'

    data, error = call_registry_api('GET', endpoint)
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data)

@app.route('/admin/attestations/api/<int:attestation_id>', methods=['GET'])
def api_get_attestation_by_id(attestation_id):
    data, error = call_registry_api('GET', f'mcp-attestation/{attestation_id}')
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data)

@app.route('/admin/attestations/api/<int:attestation_id>', methods=['POST'])
def api_perform_attestation_action(attestation_id):
    action_data = request.get_json()
    if not action_data or 'action' not in action_data:
        return jsonify({"error": "Invalid JSON payload, 'action' field is required"}), 400

    action = action_data['action']
    payload = {"action": action}

    if action == 'extend':
        if 'valid_until' not in action_data:
            return jsonify({"error": "Missing 'valid_until' for extend action"}), 400
        payload['valid_until'] = action_data['valid_until']
    elif action == 'revoke':
        # Potentially add 'reason' if API supports it
        pass
    else:
        return jsonify({"error": f"Unsupported action: {action}"}), 400

    # Assuming the registry API uses POST for actions on a specific resource ID
    data, error = call_registry_api('POST', f'mcp-attestation/{attestation_id}', data=payload)
    if error:
        # Try to parse specific error message if available
        error_msg = str(error)
        try:
            error_json = json.loads(str(error)) # Attempt to parse if error is stringified JSON
            if 'error' in error_json:
                 error_msg = error_json['error']
        except json.JSONDecodeError:
            pass # Not JSON, use the original error string

        return jsonify({"error": error_msg}), 500

    return jsonify(data)

# --- Main Server Setup ---

# In a real application, you would import these routes into your main ui_server.py
# For this self-contained example, we'll just run the app directly.

if __name__ == '__main__':
    # This part is for running this file directly for testing.
    # In ui_server.py, you would integrate these routes using app.register_blueprint or similar.
    from flask import render_template_string
    app.run(port=5001, debug=True) # Run UI server on a different port than registry API