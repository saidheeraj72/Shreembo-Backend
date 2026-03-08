from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Body
from pydantic import BaseModel

from src.core.dependencies import get_current_user, get_current_org_context
from src.api.deps.permissions import require_permission
from src.admin.service import admin_service
from src.audit.service import audit_service
from src.access.group import group_service
from src.access.permission import permission_service
from src.access.limit import limit_service
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
    UserPermissionGrant,
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


