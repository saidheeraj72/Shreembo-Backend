from uuid import UUID
from fastapi import APIRouter, Depends, status

from src.core.dependencies import get_current_user, get_current_org_context
from src.api.deps.permissions import require_permission
from src.admin.service import admin_service
from src.models.admin import InvitationCreate

router = APIRouter()


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
