from uuid import UUID
from fastapi import APIRouter, Depends

from src.core.dependencies import get_current_user, get_current_org_context
from src.api.deps.permissions import require_permission
from src.access.permission import permission_service
from src.models.admin import UserPermissionGrant

router = APIRouter()

@router.post(
    "/users/{user_id}/permissions",
    response_model=dict,
    dependencies=[Depends(require_permission("users", "manage_permissions"))],
)
async def grant_user_permission(
    user_id: UUID,
    data: UserPermissionGrant,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Grant a specific permission to a user (override role).

    **Requires:** users.manage_permissions permission
    """
    current_user_id = UUID(user["id"])

    await permission_service.grant_user_permission(
        user_id=user_id,
        permission_id=data.permission_id,
        is_granted=data.is_granted,
        granted_by=current_user_id,
    )

    return {
        "message": "Permission granted/updated successfully",
    }


@router.delete(
    "/users/{user_id}/permissions/{permission_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("users", "manage_permissions"))],
)
async def revoke_user_permission(
    user_id: UUID,
    permission_id: UUID,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Revoke a user permission override.

    **Requires:** users.manage_permissions permission
    """
    await permission_service.revoke_user_permission(
        user_id=user_id,
        permission_id=permission_id,
    )

    return {
        "message": "Permission revoked from user successfully",
    }
