"""
Organization Admin API endpoints for managing branches, users, roles, and invitations.
"""
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status

from src.core.dependencies import get_current_user, get_current_org_context
from src.api.deps.permissions import require_permission
from src.services.admin_service import admin_service
from src.services.audit_service import audit_service
from src.services.group_service import group_service
from src.models.admin import (
    Branch,
    BranchCreate,
    BranchUpdate,
    BranchWithManager,
    BranchUserAssignment,
    OrganizationMemberResponse,
    MemberUpdate,
    RoleChangeRequest,
    UserWithRole,
    InvitationCreate,
    InvitationResponse,
    RoleCreate,
    RoleUpdate,
    RoleResponse,
    RoleWithPermissions,
)
from src.models.audit import (
    AuditLogFilters,
    AuditAction,
    LogLevel,
    AuditLog,
)
from src.models.group import (
    Group,
    GroupCreate,
    GroupUpdate,
    GroupMemberAdd,
    GroupMemberRemove,
)

router = APIRouter()


# ==========================================
# AUDIT LOG ENDPOINTS
# ==========================================

@router.get(
    "/audit-logs",
    response_model=dict,
    dependencies=[Depends(require_permission("audit", "view"))],
)
async def list_audit_logs(
    action: Optional[AuditAction] = None,
    resource_type: Optional[str] = None,
    user_id: Optional[UUID] = None,
    severity: Optional[LogLevel] = None,
    page: int = 1,
    limit: int = 50,
    org_context: dict = Depends(get_current_org_context),
):
    """
    List audit logs for the organization.

    **Requires:** audit.view permission

    Query Parameters:
    - **action**: Filter by action type
    - **resource_type**: Filter by resource type
    - **user_id**: Filter by user
    - **severity**: Filter by severity
    - **page**: Page number (default: 1)
    - **limit**: Items per page (default: 50)
    """
    org_id = UUID(org_context["org_id"])
    
    # Create filters object
    filters = AuditLogFilters(
        action=action,
        resource_type=resource_type,
        user_id=user_id,
        severity=severity,
        page=page,
        limit=limit,
    )
    
    logs, total = await audit_service.get_logs(org_id, filters)

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "logs": logs,
    }


# ==========================================
# BRANCH ENDPOINTS
# ==========================================

@router.get(
    "/branches",
    response_model=dict,
    dependencies=[Depends(require_permission("branches", "view"))],
)
async def list_branches(
    include_inactive: bool = False,
    org_context: dict = Depends(get_current_org_context),
):
    """
    List all branches for the organization.

    **Requires:** branches.view permission

    Query Parameters:
    - **include_inactive**: Include inactive/deleted branches (default: false)
    """
    org_id = UUID(org_context["org_id"])
    branches = await admin_service.list_branches(org_id, include_inactive)

    return {
        "total": len(branches),
        "branches": branches,
    }


@router.get(
    "/branches/{branch_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("branches", "view"))],
)
async def get_branch(
    branch_id: UUID,
    org_context: dict = Depends(get_current_org_context),
):
    """
    Get branch details including assigned users.

    **Requires:** branches.view permission
    """
    org_id = UUID(org_context["org_id"])
    branch = await admin_service.get_branch(org_id, branch_id)
    return branch


@router.post(
    "/branches",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("branches", "create"))],
)
async def create_branch(
    data: BranchCreate,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Create a new branch.

    **Requires:** branches.create permission
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    branch = await admin_service.create_branch(
        org_id=org_id,
        data=data.model_dump(exclude_none=True),
        created_by=user_id,
    )

    return {
        "message": "Branch created successfully",
        "branch": branch,
    }


@router.put(
    "/branches/{branch_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("branches", "edit"))],
)
async def update_branch(
    branch_id: UUID,
    data: BranchUpdate,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Update a branch.

    **Requires:** branches.edit permission
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    branch = await admin_service.update_branch(
        org_id=org_id,
        branch_id=branch_id,
        data=data.model_dump(exclude_none=True),
        updated_by=user_id,
    )

    return {
        "message": "Branch updated successfully",
        "branch": branch,
    }


@router.delete(
    "/branches/{branch_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("branches", "delete"))],
)
async def delete_branch(
    branch_id: UUID,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Delete a branch (soft delete - marks as inactive).

    **Requires:** branches.delete permission
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    await admin_service.delete_branch(
        org_id=org_id,
        branch_id=branch_id,
        deleted_by=user_id,
    )

    return {
        "message": "Branch deleted successfully",
    }


@router.post(
    "/branches/{branch_id}/users",
    response_model=dict,
    dependencies=[Depends(require_permission("branches", "assign_users"))],
)
async def assign_user_to_branch(
    branch_id: UUID,
    data: BranchUserAssignment,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Assign a user to a branch.

    **Requires:** branches.assign_users permission
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    assignment = await admin_service.assign_user_to_branch(
        org_id=org_id,
        branch_id=branch_id,
        user_id=data.user_id,
        assigned_by=user_id,
        is_primary=data.is_primary,
    )

    return {
        "message": "User assigned to branch successfully",
        "assignment": assignment,
    }


@router.delete(
    "/branches/{branch_id}/users/{user_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("branches", "assign_users"))],
)
async def remove_user_from_branch(
    branch_id: UUID,
    user_id: UUID,
    current_user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Remove a user from a branch.

    **Requires:** branches.assign_users permission
    """
    org_id = UUID(org_context["org_id"])
    current_user_id = UUID(current_user["id"])

    await admin_service.remove_user_from_branch(
        org_id=org_id,
        branch_id=branch_id,
        user_id=user_id,
        removed_by=current_user_id,
    )

    return {
        "message": "User removed from branch successfully",
    }


# ==========================================
# USER ENDPOINTS
# ==========================================

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


# ==========================================
# INVITATION ENDPOINTS
# ==========================================

@router.get(
    "/invitations",
    response_model=dict,
    dependencies=[Depends(require_permission("users", "invite"))],
)
async def list_invitations(
    status: Optional[str] = None,
    org_context: dict = Depends(get_current_org_context),
):
    """
    List organization invitations.

    **Requires:** users.invite permission

    Query Parameters:
    - **status**: Filter by status (pending, accepted, expired, cancelled)
    """
    org_id = UUID(org_context["org_id"])
    invitations = await admin_service.list_invitations(org_id, status)

    return {
        "total": len(invitations),
        "invitations": invitations,
    }


@router.post(
    "/invitations",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("users", "invite"))],
)
async def create_invitation(
    data: InvitationCreate,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Send an invitation to join the organization.

    **Requires:** users.invite permission

    The invited user will receive an email with a link to accept the invitation.
    Invitations expire after 7 days.
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    invitation = await admin_service.create_invitation(
        org_id=org_id,
        email=data.email,
        role_id=data.role_id,
        invited_by=user_id,
        branch_id=data.branch_id,
        message=data.message,
    )

    return {
        "message": "Invitation sent successfully",
        "invitation": invitation,
    }


@router.delete(
    "/invitations/{invitation_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("users", "invite"))],
)
async def cancel_invitation(
    invitation_id: UUID,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Cancel a pending invitation.

    **Requires:** users.invite permission
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    await admin_service.cancel_invitation(
        org_id=org_id,
        invitation_id=invitation_id,
        cancelled_by=user_id,
    )

    return {
        "message": "Invitation cancelled successfully",
    }


@router.post(
    "/invitations/{invitation_id}/resend",
    response_model=dict,
    dependencies=[Depends(require_permission("users", "invite"))],
)
async def resend_invitation(
    invitation_id: UUID,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Resend an invitation email.

    **Requires:** users.invite permission

    This generates a new invitation token and extends the expiry by 7 days.
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    invitation = await admin_service.resend_invitation(
        org_id=org_id,
        invitation_id=invitation_id,
        resent_by=user_id,
    )

    return {
        "message": "Invitation resent successfully",
        "invitation": invitation,
    }


# ==========================================
# ROLE ENDPOINTS
# ==========================================

@router.get(
    "/roles",
    response_model=dict,
    dependencies=[Depends(require_permission("roles", "view"))],
)
async def list_roles(
    org_context: dict = Depends(get_current_org_context),
):
    """
    List all roles for the organization.

    **Requires:** roles.view permission

    Returns roles with permission and user counts.
    """
    org_id = UUID(org_context["org_id"])
    roles = await admin_service.list_roles(org_id)

    return {
        "total": len(roles),
        "roles": roles,
    }


@router.get(
    "/roles/{role_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("roles", "view"))],
)
async def get_role(
    role_id: UUID,
    org_context: dict = Depends(get_current_org_context),
):
    """
    Get role details with full permissions list.

    **Requires:** roles.view permission
    """
    org_id = UUID(org_context["org_id"])
    role = await admin_service.get_role_with_permissions(org_id, role_id)
    return role


@router.post(
    "/roles",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("roles", "create"))],
)
async def create_role(
    data: RoleCreate,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Create a custom role.

    **Requires:** roles.create permission

    Provide a list of permission IDs to assign to the role.
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    role = await admin_service.create_role(
        org_id=org_id,
        data=data.model_dump(),
        created_by=user_id,
    )

    return {
        "message": "Role created successfully",
        "role": role,
    }


@router.put(
    "/roles/{role_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("roles", "edit"))],
)
async def update_role(
    role_id: UUID,
    data: RoleUpdate,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Update a role.

    **Requires:** roles.edit permission

    Note: Only the Owner role cannot be modified. Admin and Member roles can be modified.
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    role = await admin_service.update_role(
        org_id=org_id,
        role_id=role_id,
        data=data.model_dump(exclude_none=True),
        updated_by=user_id,
    )

    return {
        "message": "Role updated successfully",
        "role": role,
    }


@router.delete(
    "/roles/{role_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("roles", "delete"))],
)
async def delete_role(
    role_id: UUID,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Delete a role.

    **Requires:** roles.delete permission

    Note:
    - Only the Owner role cannot be deleted
    - Roles with assigned users cannot be deleted
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    await admin_service.delete_role(
        org_id=org_id,
        role_id=role_id,
        deleted_by=user_id,
    )

    return {
        "message": "Role deleted successfully",
    }


@router.get(
    "/permissions",
    response_model=dict,
    dependencies=[Depends(require_permission("roles", "view"))],
)
async def list_permissions(
    org_context: dict = Depends(get_current_org_context),
):
    """
    List all available permissions grouped by module.

    **Requires:** roles.view permission

    Use this to populate role permission selection UI.
    """
    modules = await admin_service.get_all_permissions()

    return {
        "total_modules": len(modules),
        "modules": modules,
    }


# ==========================================
# GROUP ENDPOINTS
# ==========================================

@router.get(
    "/groups",
    response_model=dict,
    dependencies=[Depends(require_permission("groups", "view"))],
)
async def list_groups(
    org_context: dict = Depends(get_current_org_context),
):
    """
    List all groups for the organization.

    **Requires:** groups.view permission
    """
    org_id = UUID(org_context["org_id"])
    groups = await group_service.list_groups(org_id)

    return {
        "total": len(groups),
        "groups": groups,
    }


@router.get(
    "/groups/{group_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("groups", "view"))],
)
async def get_group(
    group_id: UUID,
    org_context: dict = Depends(get_current_org_context),
):
    """
    Get group details including members.

    **Requires:** groups.view permission
    """
    org_id = UUID(org_context["org_id"])
    group = await group_service.get_group(org_id, group_id)
    return group


@router.post(
    "/groups",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("groups", "create"))],
)
async def create_group(
    data: GroupCreate,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Create a new group.

    **Requires:** groups.create permission
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    group = await group_service.create_group(
        org_id=org_id,
        data=data.model_dump(),
        created_by=user_id,
    )

    return {
        "message": "Group created successfully",
        "group": group,
    }


@router.put(
    "/groups/{group_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("groups", "edit"))],
)
async def update_group(
    group_id: UUID,
    data: GroupUpdate,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Update a group.

    **Requires:** groups.edit permission
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    group = await group_service.update_group(
        org_id=org_id,
        group_id=group_id,
        data=data.model_dump(exclude_none=True),
        updated_by=user_id,
    )

    return {
        "message": "Group updated successfully",
        "group": group,
    }


@router.delete(
    "/groups/{group_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("groups", "delete"))],
)
async def delete_group(
    group_id: UUID,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Delete a group.

    **Requires:** groups.delete permission
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    await group_service.delete_group(
        org_id=org_id,
        group_id=group_id,
        deleted_by=user_id,
    )

    return {
        "message": "Group deleted successfully",
    }


@router.post(
    "/groups/{group_id}/members",
    response_model=dict,
    dependencies=[Depends(require_permission("groups", "manage_members"))],
)
async def add_group_members(
    group_id: UUID,
    data: GroupMemberAdd,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Add members to a group.

    **Requires:** groups.manage_members permission
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    await group_service.add_members(
        org_id=org_id,
        group_id=group_id,
        user_ids=data.user_ids,
        added_by=user_id,
    )

    return {
        "message": "Members added successfully",
    }


@router.delete(
    "/groups/{group_id}/members",
    response_model=dict,
    dependencies=[Depends(require_permission("groups", "manage_members"))],
)
async def remove_group_members(
    group_id: UUID,
    data: GroupMemberRemove,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Remove members from a group.

    **Requires:** groups.manage_members permission
    """
    org_id = UUID(org_context["org_id"])
    user_id = UUID(user["id"])

    await group_service.remove_members(
        org_id=org_id,
        group_id=group_id,
        user_ids=data.user_ids,
        removed_by=user_id,
    )

    return {
        "message": "Members removed successfully",
    }
