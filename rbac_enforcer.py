# rbac_enforcer.py

# Predefined RBAC policies.
# Structure:
# {
#   "role_name": {
#     "resource_name": ["allowed_action_1", "allowed_action_2", ...],
#     ...
#   },
#   ...
# }
POLICIES = {
    "admin": {
        "resource1": ["read", "write", "delete"],
        "resource2": ["read", "write"],
        "logs": ["read", "clear"],
        "settings": ["read", "update"]
    },
    "user": {
        "resource1": ["read"],
        "resource2": ["read", "write"],
        "profile": ["read", "update"]
    },
    "guest": {
        "resource1": ["read"]
    }
}

def enforce_policy(user_role: str, resource: str, action: str) -> bool:
    """
    Enforces RBAC policy by checking if a user_role is allowed to perform
    a specific action on a given resource based on predefined policies.

    Args:
        user_role (str): The role of the user (e.g., 'admin', 'user').
        resource (str): The resource being accessed (e.g., 'resource1', 'logs').
        action (str): The action being performed (e.g., 'read', 'write', 'delete').

    Returns:
        bool: True if the action is allowed for the given role and resource,
              False otherwise.
    """
    # Check if the user_role exists in our predefined policies
    if user_role not in POLICIES:
        return False

    role_permissions = POLICIES[user_role]

    # Check if the resource is defined for this role
    if resource not in role_permissions:
        return False

    allowed_actions = role_permissions[resource]

    # Check if the requested action is among the allowed actions for this resource
    if action in allowed_actions:
        return True
    else:
        return False

# Self-test block for acceptance criteria and additional tests
if __name__ == "__main__":
    print("Running RBAC Enforcer Self-Test...")

    # --- Acceptance Test ---
    print("\n--- Acceptance Test ---")
    acceptance_test_result = enforce_policy('admin', 'resource1', 'read')
    print(f"Calling enforce_policy('admin', 'resource1', 'read') -> {acceptance_test_result}")
    assert acceptance_test_result is True, "ACCEPTANCE TEST FAILED: Expected True for admin, resource1, read"
    print("ACCEPTANCE TEST PASSED.")

    # --- Additional Test Cases ---
    print("\n--- Additional Test Cases ---")

    # Test Case 1: Admin writing to resource1 (should be True)
    test1 = enforce_policy('admin', 'resource1', 'write')
    print(f"admin, resource1, write -> {test1}")
    assert test1 is True, "Test Case 1 Failed: Admin should be able to write resource1"

    # Test Case 2: User reading resource1 (should be True)
    test2 = enforce_policy('user', 'resource1', 'read')
    print(f"user, resource1, read -> {test2}")
    assert test2 is True, "Test Case 2 Failed: User should be able to read resource1"

    # Test Case 3: User writing to resource1 (should be False)
    test3 = enforce_policy('user', 'resource1', 'write')
    print(f"user, resource1, write -> {test3}")
    assert test3 is False, "Test Case 3 Failed: User should NOT be able to write resource1"

    # Test Case 4: Guest reading resource1 (should be True)
    test4 = enforce_policy('guest', 'resource1', 'read')
    print(f"guest, resource1, read -> {test4}")
    assert test4 is True, "Test Case 4 Failed: Guest should be able to read resource1"

    # Test Case 5: Guest writing to resource1 (should be False)
    test5 = enforce_policy('guest', 'resource1', 'write')
    print(f"guest, resource1, write -> {test5}")
    assert test5 is False, "Test Case 5 Failed: Guest should NOT be able to write resource1"

    # Test Case 6: Unknown role accessing resource (should be False)
    test6 = enforce_policy('super_user', 'resource1', 'read')
    print(f"super_user, resource1, read -> {test6}")
    assert test6 is False, "Test Case 6 Failed: Unknown role should not have access"

    # Test Case 7: Known role accessing unknown resource (should be False)
    test7 = enforce_policy('admin', 'unknown_resource', 'read')
    print(f"admin, unknown_resource, read -> {test7}")
    assert test7 is False, "Test Case 7 Failed: Admin should not have access to unknown resource"

    # Test Case 8: User writing to resource2 (should be True based on policy)
    test8 = enforce_policy('user', 'resource2', 'write')
    print(f"user, resource2, write -> {test8}")
    assert test8 is True, "Test Case 8 Failed: User should be able to write resource2"

    print("\nAll self-tests completed successfully.")
    print("PASS")