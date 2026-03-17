"""
Permission-related dependencies for routes.
"""
from uuid import UUID
from fastapi import Depends, HTTPException, status

from src.core.dependencies import get_current_user, get_current_org_context
from src.access.permission import permission_service
from src.super_admin.service import super_admin_service


def require_permission(module: str, action: str):
    """
    Dependency factory for permission-based route protection.

    Usage:
        @router.get("/admin/users", dependencies=[Depends(require_permission("users", "view"))])

    Args:
        module: Permission module (e.g., "users", "documents")
        action: Permission action (e.g., "view", "create", "edit")

    Returns:
        FastAPI dependency function
    """

    async def permission_checker(
        user: dict = Depends(get_current_user),
        org_context: dict = Depends(get_current_org_context),
    ) -> bool:
        """Check if user has the required permission."""
        user_id = UUID(user["id"])
        org_id = org_context.get("org_id")

        if not org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organization membership required for this action",
            )

        has_permission = await permission_service.check_permission(
            user_id=user_id,
            org_id=UUID(org_id),
            module=module,
            action=action,
        )

        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have the necessary permissions to perform this action.",
            )

        return True

    return permission_checker


async def require_super_admin(
    user: dict = Depends(get_current_user),
) -> dict:
    """
    Dependency to require super admin access.

    Usage:
        @router.get("/super-admin/...", dependencies=[Depends(require_super_admin)])

    Args:
        user: Current user from JWT token

    Returns:
        User data if super admin

    Raises:
        HTTPException: If user is not a super admin
    """
    email = user.get("email")

    if not email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required",
        )

    is_super_admin = await super_admin_service.verify_super_admin(email)

    if not is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required",
        )

    # Update last login timestamp
    await super_admin_service.update_last_login(email)

    return user
