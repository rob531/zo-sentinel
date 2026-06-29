# deps: fastapi, pydantic, PyJWT, sqlalchemy
"""
rbac_enforcer – Tier-1 app-foundation RBAC module.

Provides
--------
require_role(min_role: str)
    FastAPI dependency.  Raises 403 when the authenticated principal's role rank
    is below *min_role*.  Roles (lowest→highest): viewer < member < admin.

has_permission(role: str, action: str) -> bool
    Predicate used by callers that need to branch on permission without raising.
    Currently maps every action to "member" (all actions require at least member).

Usage in a router endpoint
--------------------------
    @router.post("/servers", dependencies=[Depends(require_role("member"))])
    def create_server(...): ...

    @router.delete("/servers/{id}", dependencies=[Depends(require_role("admin"))])
    def delete_server(...): ...

No database writes, no network I/O – pure Python.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# --------------------------------------------------------------------------- #
# Re-export the existing server-side dependency from app/rbac.py
# --------------------------------------------------------------------------- #
from app.rbac import require_role as _require_role  # noqa: E402

# Keep the original name exposed so callers use require_role(...) as documented
require_role = _require_role


# --------------------------------------------------------------------------- #
# Permission predicate
# --------------------------------------------------------------------------- #
# Minimal action→min_role map.  Extend here when new action constants appear.
_ACTION_MIN_ROLE: dict[str, str] = {
    # Every action in the Tier-1 spec requires at least "member".
    "read":       "member",
    "write":      "member",
    "delete":     "admin",
    "admin":      "admin",
    # Sentinel-specific
    "submit_dispute":   "member",
    "resolve_dispute":  "admin",
}


def has_permission(role: str, action: str) -> bool:
    """
    Return True when *role* is sufficient to perform *action*.
    If *action* is unknown it is treated as "admin" (fail-closed).
    """
    min_role = _ACTION_MIN_ROLE.get(action, "admin")
    # Reuse the rank logic from app/rbac so role semantics stay in one place.
    from app.rbac import role_rank as _rank
    return _rank(role) >= _rank(min_role)


# --------------------------------------------------------------------------- #
# Self-test (run with:  python rbac_enforcer.py)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # We need a live app to attach TestClient.  Build a minimal one.
    # Import the real DB / models only for the test session.
    from app.db import get_session, Base  # noqa: E402
    from app.models import Org, User  # noqa: E402
    from app.security import hash_password, create_access_token  # noqa: E402

    # In-memory SQLite so the test needs no Postgres
    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(_engine)
    _TestSession = sessionmaker(bind=_engine, expire_on_commit=False)

    def _test_session() -> Session:
        return _TestSession()

    app = FastAPI()
    app.dependency_overrides[get_session] = _test_session

    # Bootstrap a test org + users
    with _TestSession() as sess:
        org = Org(id="test-org", name="Test Org")
        admin = User(
            id="uid-admin", email="admin@test.com",
            password_hash=hash_password("AdminPass1!"),
            org_id="test-org", role="admin",
        )
        member = User(
            id="uid-member", email="member@test.com",
            password_hash=hash_password("MemberPass1!"),
            org_id="test-org", role="member",
        )
        sess.add_all([org, admin, member])
        sess.commit()

    # Tokens
    admin_token = create_access_token("uid-admin", "test-org", "admin")
    member_token = create_access_token("uid-member", "test-org", "member")

    # Protected endpoints using the require_role dependency
    @app.get("/admin-only", dependencies=[Depends(require_role("admin"))])
    def admin_only():
        return {"msg": "admin ok"}

    @app.get("/member-or-above", dependencies=[Depends(require_role("member"))])
    def member_or_above():
        return {"msg": "member ok"}

    client = TestClient(app, raise_server_exceptions=False)

    # ----- happy path -----
    r_admin  = client.get("/admin-only", headers={"Authorization": f"Bearer {admin_token}"})
    r_member = client.get("/member-or-above", headers={"Authorization": f"Bearer {member_token}"})
    r_admin_member_token = client.get("/admin-only", headers={"Authorization": f"Bearer {member_token}"})
    r_anon   = client.get("/admin-only")

    ok_admin  = r_admin.status_code == 200
    ok_member = r_member.status_code == 200
    ok_blocked = r_admin_member_token.status_code == 403   # member can't reach admin-only
    ok_anon   = r_anon.status_code in (401, 403)          # no token

    # ----- has_permission checks -----
    ok_perm_admin_delete   = has_permission("admin", "delete") is True
    ok_perm_admin_read    = has_permission("admin", "read") is True
    ok_perm_member_delete = has_permission("member", "delete") is False
    ok_perm_member_read   = has_permission("member", "read") is True
    ok_perm_unknown       = has_permission("admin", "unknown") is True   # admin always ok
    ok_perm_unk_member    = has_permission("member", "unknown") is False  # fail-closed

    passed = (
        ok_admin and ok_member and ok_blocked and ok_anon
        and ok_perm_admin_delete and ok_perm_admin_read
        and ok_perm_member_delete and ok_perm_member_read
        and ok_perm_unknown and ok_perm_unk_member
    )

    if passed:
        print("PASS")
    else:
        print(
            "FAIL: "
            f"admin_get={ok_admin} member_get={ok_member} "
            f"blocked={ok_blocked} anon={ok_anon} "
            f"perm_adm_del={ok_perm_admin_delete} perm_adm_rd={ok_perm_admin_read} "
            f"perm_mem_del={ok_perm_member_delete} perm_mem_rd={ok_perm_member_read} "
            f"perm_unk_adm={ok_perm_unknown} perm_unk_mem={ok_perm_unk_member}"
        )
