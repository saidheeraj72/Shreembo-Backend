from uuid import UUID
from fastapi import APIRouter, Depends, status

from src.core.dependencies import get_current_user, get_current_org_context
from src.api.deps.permissions import require_permission
from src.admin.service import admin_service
from src.models.admin import RoleCreate, RoleUpdate

router = APIRouter()

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


