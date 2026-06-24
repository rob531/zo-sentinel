"""Server-side RBAC: an ordered role hierarchy + `require_role(min_role)` FastAPI
dependency that 403s on insufficient role, and `require_org` for tenant isolation.
Enforced as dependencies on the routers -- never a client-side check.
"""
from __future__ import annotations
from fastapi import Depends, HTTPException, status

from .security import Principal, get_principal

ROLE_RANK = {"viewer": 1, "member": 2, "admin": 3}


def role_rank(role: str) -> int:
    return ROLE_RANK.get((role or "").lower(), 0)


def require_role(min_role: str):
    """Return a dependency that admits only principals whose role rank >= min_role."""
    min_rank = ROLE_RANK.get(min_role, 99)

    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if role_rank(principal.role) < min_rank:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"requires role >= {min_role}")
        return principal

    return _dep


def require_org(resource_org_id: str, principal: Principal) -> Principal:
    """Tenant isolation: 403 unless the principal belongs to the resource's org."""
    if principal.org_id != resource_org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="cross-org access denied")
    return principal
