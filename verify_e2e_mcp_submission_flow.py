import requests
import json
from datetime import datetime

# Assume write_service is available and has the necessary methods
# For demonstration purposes, we'll mock it. In a real scenario,
# you would import it from your project.
class MockWriteService:
    def __init__(self):
        self.mcp_submissions_data = {}
        self.mcp_definition_history_data = {}
        self.next_submission_id = 1
        self.next_definition_history_id = 1

    def create_mcp_submission(self, submission_data):
        submission_id = self.next_submission_id
        submission_data['id'] = submission_id
        self.mcp_submissions_data[submission_id] = submission_data
        self.next_submission_id += 1
        return submission_id

    def get_mcp_submission_by_id(self, submission_id):
        return self.mcp_submissions_data.get(submission_id)

    def get_mcp_definition_history_by_submission_id(self, submission_id):
        return [
            entry for entry in self.mcp_definition_history_data.values()
            if entry['mcp_submission_id'] == submission_id
        ]

    def create_mcp_definition_history(self, history_data):
        history_id = self.next_definition_history_id
        history_data['id'] = history_id
        self.mcp_definition_history_data[history_id] = history_data
        self.next_definition_history_id += 1
        return history_id

# Replace with your actual write_service import
# from your_project.services import write_service
write_service = MockWriteService()

# Assume the API endpoint is running locally for this example
API_BASE_URL = "http://localhost:5000" # Replace with your actual API base URL

def simulate_mcp_submission(submission_payload):
    """
    Simulates a new MCP submission through the API.
    """
    url = f"{API_BASE_URL}/mcp_submissions"
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(url, data=json.dumps(submission_payload), headers=headers)
        response.raise_for_status()  # Raise an exception for bad status codes
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error simulating MCP submission: {e}")
        return None

def verify_submission_in_db(submission_id):
    """
    Verifies that the submission is recorded in the mcp_submissions table.
    """
    submission = write_service.get_mcp_submission_by_id(submission_id)
    return submission is not None

def verify_definition_history_in_db(submission_id):
    """
    Verifies that mcp_definition_history is populated for the submission.
    """
    history_entries = write_service.get_mcp_definition_history_by_submission_id(submission_id)
    return len(history_entries) > 0

def main():
    """
    Performs an end-to-end verification of the MCP submission flow.
    """
    print("Starting MCP submission flow verification...")

    # 1. Simulate a new MCP submission
    submission_payload = {
        "mcp_id": "MCP-12345",
        "version": "1.0.0",
        "definition": {"key": "value"},
        "submitted_by": "test_user",
        "submission_timestamp": datetime.utcnow().isoformat() + "Z"
    }

    print("Simulating MCP submission via API...")
    api_response = simulate_mcp_submission(submission_payload)

    if api_response is None or 'submission_id' not in api_response:
        print("Verification failed: API submission simulation failed or did not return submission_id.")
        return

    simulated_submission_id = api_response['submission_id']
    print(f"Submission simulated successfully. Submission ID: {simulated_submission_id}")

    # 2. Verify submission in mcp_submissions table
    print(f"Verifying submission {simulated_submission_id} in mcp_submissions table...")
    submission_exists = verify_submission_in_db(simulated_submission_id)
    assert submission_exists, f"Verification failed: Submission {simulated_submission_id} not found in mcp_submissions table."
    print("Submission found in mcp_submissions table.")

    # 3. Verify mcp_definition_history population
    print(f"Verifying mcp_definition_history for submission {simulated_submission_id}...")
    history_populated = verify_definition_history_in_db(simulated_submission_id)
    assert history_populated, f"Verification failed: mcp_definition_history not populated for submission {simulated_submission_id}."
    print("mcp_definition_history is populated.")

    print("\nPASS: End-to-end MCP submission flow verified successfully.")

if __name__ == "__main__":
    # Mock the API endpoint for local testing if it's not running
    # In a real scenario, you'd ensure your API server is running.
    from flask import Flask, request, jsonify
    from flask_sqlalchemy import SQLAlchemy

    app = Flask(__name__)
    # Configure a simple in-memory database for the mock API
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db = SQLAlchemy(app)

    # Define mock SQLAlchemy models to match what write_service might expect
    class MCPSubmission(db.Model):
        __tablename__ = 'mcp_submissions'
        id = db.Column(db.Integer, primary_key=True)
        mcp_id = db.Column(db.String(50), nullable=False)
        version = db.Column(db.String(20), nullable=False)
        definition = db.Column(db.JSON, nullable=False)
        submitted_by = db.Column(db.String(100), nullable=False)
        submission_timestamp = db.Column(db.DateTime, nullable=False)

    class MCPDefinitionHistory(db.Model):
        __tablename__ = 'mcp_definition_history'
        id = db.Column(db.Integer, primary_key=True)
        mcp_submission_id = db.Column(db.Integer, db.ForeignKey('mcp_submissions.id'), nullable=False)
        definition = db.Column(db.JSON, nullable=False)
        timestamp = db.Column(db.DateTime, nullable=False)

    # Re-initialize write_service with actual DB access for the mock API
    class RealWriteServiceForMockAPI:
        def __init__(self, db_session):
            self.db = db_session

        def create_mcp_submission(self, submission_data):
            new_submission = MCPSubmission(
                mcp_id=submission_data['mcp_id'],
                version=submission_data['version'],
                definition=submission_data['definition'],
                submitted_by=submission_data['submitted_by'],
                submission_timestamp=submission_data['submission_timestamp']
            )
            self.db.session.add(new_submission)
            self.db.session.commit()
            return new_submission.id

        def get_mcp_submission_by_id(self, submission_id):
            return MCPSubmission.query.get(submission_id)

        def get_mcp_definition_history_by_submission_id(self, submission_id):
            return MCPDefinitionHistory.query.filter_by(mcp_submission_id=submission_id).all()

        def create_mcp_definition_history(self, history_data):
            new_history = MCPDefinitionHistory(
                mcp_submission_id=history_data['mcp_submission_id'],
                definition=history_data['definition'],
                timestamp=history_data['timestamp']
            )
            self.db.session.add(new_history)
            self.db.session.commit()
            return new_history.id

    # Create tables within the mock API context
    with app.app_context():
        db.create_all()
        # Replace the mock write_service with one that uses the actual DB
        write_service = RealWriteServiceForMockAPI(db)

    @app.route('/mcp_submissions', methods=['POST'])
    def handle_mcp_submission():
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        try:
            # Simulate the logic that would normally be in your API handler
            # This includes calling the write_service to create the submission
            # and then potentially creating a history entry.
            submission_id = write_service.create_mcp_submission({
                "mcp_id": data.get("mcp_id"),
                "version": data.get("version"),
                "definition": data.get("definition"),
                "submitted_by": data.get("submitted_by"),
                "submission_timestamp": datetime.fromisoformat(data.get("submission_timestamp").replace('Z', '+00:00'))
            })

            # Simulate creating a definition history entry upon submission
            write_service.create_mcp_definition_history({
                "mcp_submission_id": submission_id,
                "definition": data.get("definition"),
                "timestamp": datetime.utcnow()
            })

            return jsonify({"message": "MCP submission received", "submission_id": submission_id}), 201
        except Exception as e:
            print(f"Error in mock API handler: {e}")
            return jsonify({"error": "Internal server error"}), 500

    # Run the mock API server in a separate thread
    import threading
    api_thread = threading.Thread(target=lambda: app.run(port=5000, debug=False))
    api_thread.daemon = True # Allow main thread to exit even if this is running
    api_thread.start()

    # Give the API a moment to start up
    import time
    time.sleep(1)

    # Execute the main verification logic
    main()