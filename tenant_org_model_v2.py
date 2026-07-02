import requests
import json

class TenantOrgModelV2:
    def __init__(self, write_service_url="http://localhost:8772/write"):
        self.write_service_url = write_service_url

    def org_scope(self, sql, org_id):
        """Scope SQL queries with org_id filter."""
        if "WHERE" in sql.upper():
            return f"{sql} AND org_id = {org_id}"
        else:
            return f"{sql} WHERE org_id = {org_id}"

    def create_org(self, name):
        """Create an organization and return its ID."""
        payload = {
            "table": "organizations",
            "data": {"name": name}
        }
        response = requests.post(self.write_service_url, json=payload)
        if response.status_code == 200:
            return response.json()["id"]
        else:
            raise Exception(f"Failed to create organization: {response.text}")

    def add_member(self, org_id, user_id, role):
        """Add a member to an organization."""
        payload = {
            "table": "org_members",
            "data": {"org_id": org_id, "user_id": user_id, "role": role}
        }
        response = requests.post(self.write_service_url, json=payload)
        if response.status_code != 200:
            raise Exception(f"Failed to add member: {response.text}")

    def list_members(self, org_id):
        """List members of an organization."""
        payload = {
            "table": "org_members",
            "filter": {"org_id": org_id}
        }
        response = requests.post(self.write_service_url, json=payload)
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to list members: {response.text}")

    def remove_member(self, org_id, user_id):
        """Remove a member from an organization."""
        payload = {
            "table": "org_members",
            "filter": {"org_id": org_id, "user_id": user_id}
        }
        response = requests.post(self.write_service_url, json=payload)
        if response.status_code != 200:
            raise Exception(f"Failed to remove member: {response.text}")

if __name__ == "__main__":
    # Acceptance test
    model = TenantOrgModelV2()

    # Create an organization
    org_id = model.create_org("Test Org")

    # Add a member
    model.add_member(org_id, 1, "admin")

    # Test org_scope
    sql = "SELECT * FROM users"
    scoped_sql = model.org_scope(sql, org_id)
    assert scoped_sql == "SELECT * FROM users WHERE org_id = 1"

    # List members
    members = model.list_members(org_id)
    assert len(members) == 1
    assert members[0]["user_id"] == 1

    # Remove member
    model.remove_member(org_id, 1)
    members = model.list_members(org_id)
    assert len(members) == 0

    print("PASS")