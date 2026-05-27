from uuid import UUID
from fastapi import APIRouter, Depends, status

from src.core.dependencies import get_current_user, get_current_org_context
from src.api.deps.permissions import require_permission
from src.admin.service import admin_service
from src.models.admin import BranchCreate, BranchUpdate

router = APIRouter()

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
