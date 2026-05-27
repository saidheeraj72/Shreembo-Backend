from uuid import UUID
from fastapi import APIRouter, Depends

from src.core.dependencies import get_current_user, get_current_org_context
from src.api.deps.permissions import require_permission
from src.access.permission import permission_service

router = APIRouter()

# ==========================================
# FOLDER ACCESS ENDPOINTS
# ==========================================

@router.get(
    "/users/{user_id}/folder-access",
    response_model=dict,
    dependencies=[Depends(require_permission("users", "view"))],
)
async def get_user_folder_access(
    user_id: UUID,
    org_context: dict = Depends(get_current_org_context),
):
    """
    Get all folders a user has explicit access to.

    **Requires:** users.view permission
    """
    org_id = UUID(org_context["org_id"])
    folder_access = await permission_service.get_user_folder_access(user_id, org_id)

    return {
        "total": len(folder_access),
        "folder_access": folder_access,
    }


@router.post(
    "/users/{user_id}/folder-access",
    response_model=dict,
    dependencies=[Depends(require_permission("users", "manage_permissions"))],
)
async def grant_user_folder_access(
    user_id: UUID,
    folder_id: UUID,
    permission: str = "view",
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Grant a user access to a specific folder.

    **Requires:** users.manage_permissions permission

    Query Parameters:
    - **folder_id**: The folder UUID to grant access to
    - **permission**: Permission level (view, edit, admin). Default: view
    """
    org_id = UUID(org_context["org_id"])
    current_user_id = UUID(user["id"])

    result = await permission_service.grant_folder_access(
        user_id=user_id,
        folder_id=folder_id,
        org_id=org_id,
        granted_by=current_user_id,
        permission=permission,
    )

    return {
        "message": "Folder access granted successfully",
        "access": result,
    }


@router.delete(
    "/users/{user_id}/folder-access/{folder_id}",
    response_model=dict,
    dependencies=[Depends(require_permission("users", "manage_permissions"))],
)
async def revoke_user_folder_access(
    user_id: UUID,
    folder_id: UUID,
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Revoke a user's access to a specific folder.

    **Requires:** users.manage_permissions permission
    """
    await permission_service.revoke_folder_access(
        user_id=user_id,
        folder_id=folder_id,
    )

    return {
        "message": "Folder access revoked successfully",
    }


@router.get(
    "/folders-tree",
    response_model=dict,
    dependencies=[Depends(require_permission("documents", "view"))],
)
async def get_folders_tree(
    org_context: dict = Depends(get_current_org_context),
):
    """
    Get all folders and files in the organization as a flat list for permission assignment.

    **Requires:** documents.view permission
    """
    from src.core.database import db

    org_id = UUID(org_context["org_id"])

    # Get all branches
    branches_result = db.admin.table("branches").select(
        "id, name"
    ).eq("org_id", str(org_id)).eq("is_active", True).execute()

    # Get all folders and files
    folders_result = db.admin.table("storage_nodes").select(
        "id, name, parent_id, branch_id, node_type, mime_type"
    ).eq("org_id", str(org_id)).eq("status", "active").execute()

    return {
        "branches": branches_result.data,
        "folders": folders_result.data,
    }

