from typing import Optional
from uuid import UUID
from fastapi import HTTPException

from src.access.permission import permission_service


async def check_resource_access(
    user_id: UUID,
    org_id: Optional[str],
    node_id: Optional[UUID],
    required_level: str = "edit",
):
    """Helper to check resource access for org users."""
    if not org_id:
        return

    has_access = await permission_service.check_folder_permission(
        user_id=user_id,
        folder_id=node_id,
        org_id=UUID(org_id),
        required_level=required_level,
    )
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"You do not have {required_level} permission on this folder/document",
        )


async def check_general_permission(
    user_id: UUID,
    org_id: Optional[str],
    action: str,
):
    """Helper to check general document permissions."""
    if not org_id:
        return

    has_perm = await permission_service.check_permission(
        user_id=user_id,
        org_id=UUID(org_id),
        module="documents",
        action=action,
    )
    if not has_perm:
        raise HTTPException(
            status_code=403,
            detail=f"You do not have permission to {action} documents",
        )
