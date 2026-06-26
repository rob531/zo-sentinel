import requests

class VerifyMCPDefinitionHistoryFromSubmissionsFlow:
    def __init__(self, write_service_url):
        self.write_service_url = write_service_url

    def get_recent_submissions(self):
        response = requests.get(f"{self.write_service_url}/mcp_submissions")
        response.raise_for_status()
        return response.json()

    def get_definition_history(self, submission_id):
        response = requests.get(f"{self.write_service_url}/mcp_definition_history?submission_id={submission_id}")
        response.raise_for_status()
        return response.json()

    def verify_data_flow(self, submissions):
        discrepancies = []
        for submission in submissions:
            submission_id = submission['id']
            definition_history = self.get_definition_history(submission_id)
            if not definition_history:
                discrepancies.append(f"No definition history found for submission ID: {submission_id}")
                continue

            for history in definition_history:
                if history['submission_id'] != submission_id:
                    discrepancies.append(f"Submission ID mismatch for history ID: {history['id']}")
                if history['data'] != submission['data']:
                    discrepancies.append(f"Data mismatch for submission ID: {submission_id}")

        return discrepancies

    def run(self):
        submissions = self.get_recent_submissions()
        discrepancies = self.verify_data_flow(submissions)

        if discrepancies:
            print("FAIL: Data integrity check failed with the following discrepancies:")
            for discrepancy in discrepancies:
                print(f"- {discrepancy}")
        else:
            print("PASS: Data integrity check passed for all submissions.")

if __name__ == "__main__":
    write_service_url = "http://localhost:5000"  # Replace with actual write service URL
    verifier = VerifyMCPDefinitionHistoryFromSubmissionsFlow(write_service_url)
    verifier.run()