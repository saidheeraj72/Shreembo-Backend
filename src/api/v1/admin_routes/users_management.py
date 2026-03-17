from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends

from src.core.dependencies import get_current_user, get_current_org_context
from src.api.deps.permissions import require_permission
from src.admin.service import admin_service
from src.models.admin import MemberUpdate, RoleChangeRequest

router = APIRouter()

@router.get(
    "/users",
    response_model=dict,
    dependencies=[Depends(require_permission("users", "view"))],
)
async def list_users(
    status: Optional[str] = None,
    org_context: dict = Depends(get_current_org_context),
):
    """
    List all organization members.

    **Requires:** users.view permission

    Query Parameters:
    - **status**: Filter by member status (active, invited, suspended, removed)
    """
    org_id = UUID(org_context["org_id"])
    users = await admin_service.list_org_users(org_id, status)

    return {
        "total": len(users),
        "users": users,
    }


@router.get(
    "/users/{user_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("users", "view"))],
)
async def get_user_details(
    user_id: UUID,
    org_context: dict = Depends(get_current_org_context),
):
    """
    Get detailed information about a user.

    **Requires:** users.view permission

    Returns user profile, role, permissions, and branch assignments.
    """
    org_id = UUID(org_context["org_id"])
    user = await admin_service.get_user_details(org_id, user_id)
    return user


@router.put(
    "/users/{user_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("users", "edit"))],
)
async def update_user(
    user_id: UUID,
    data: MemberUpdate,
    current_user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Update organization member details (title, department, etc.).

    **Requires:** users.edit permission
    """
    org_id = UUID(org_context["org_id"])
    current_user_id = UUID(current_user["id"])

    member = await admin_service.update_member(
        org_id=org_id,
        user_id=user_id,
        data=data.model_dump(exclude_none=True),
        updated_by=current_user_id,
    )

    return {
        "message": "User updated successfully",
        "member": member,
    }


@router.put(
    "/users/{user_id}/role",
    response_model=dict,
    dependencies=[Depends(require_permission("users", "assign_roles"))],
)
async def change_user_role(
    user_id: UUID,
    data: RoleChangeRequest,
    current_user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Change a user's role in the organization.

    **Requires:** users.assign_roles permission
    """
    org_id = UUID(org_context["org_id"])
    current_user_id = UUID(current_user["id"])

    member = await admin_service.change_user_role(
        org_id=org_id,
        user_id=user_id,
        role_id=data.role_id,
        changed_by=current_user_id,
    )

    return {
        "message": "User role changed successfully",
        "member": member,
    }
@router.delete(
    "/users/{user_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("users", "remove"))],
)
async def remove_user(
    user_id: UUID,
    current_user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Remove a user from the organization.

    **Requires:** users.remove permission

    Note: Organization owner cannot be removed.
    """
    org_id = UUID(org_context["org_id"])
    current_user_id = UUID(current_user["id"])

    await admin_service.remove_member(
        org_id=org_id,
        user_id=user_id,
        removed_by=current_user_id,
    )

    return {
        "message": "User removed from organization successfully",
    }
