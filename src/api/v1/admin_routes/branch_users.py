from uuid import UUID
from fastapi import APIRouter, Depends

from src.core.dependencies import get_current_user, get_current_org_context
from src.api.deps.permissions import require_permission
from src.admin.service import admin_service
from src.models.admin import BranchUserAssignment

router = APIRouter()

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
