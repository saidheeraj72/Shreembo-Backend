from uuid import UUID
from fastapi import APIRouter, Depends

from src.core.dependencies import get_current_user, get_current_org_context
from src.api.deps.permissions import require_permission
from src.access.group import group_service
from src.models.group import GroupMemberAdd, GroupMemberRemove

router = APIRouter()

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

    updated_group = await group_service.get_group(org_id, group_id)

    return {
        "message": "Members added successfully",
        "group": updated_group,
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

    updated_group = await group_service.get_group(org_id, group_id)

    return {
        "message": "Members removed successfully",
        "group": updated_group,
    }
