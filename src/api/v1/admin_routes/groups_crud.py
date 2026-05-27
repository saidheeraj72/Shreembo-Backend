from uuid import UUID
from fastapi import APIRouter, Depends, status

from src.core.dependencies import get_current_user, get_current_org_context
from src.api.deps.permissions import require_permission
from src.access.group import group_service
from src.models.group import GroupCreate

router = APIRouter()

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
