from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, Query, HTTPException

from src.core.dependencies import get_current_user_id, get_current_org_context
from src.documents.service import document_service
from src.models.document import FolderCreate, FolderUpdate, FolderResponse, FolderContents, NodeType
from src.api.v1.document_routes.common import check_resource_access, check_general_permission

router = APIRouter()

# Folders
@router.post("/folders", response_model=FolderResponse)
async def create_folder(
    data: FolderCreate,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Create a new folder."""
    org_id = org_context.get("org_id")
    
    # Check permissions
    await check_general_permission(user_id, org_id, "create")
    
    if data.parent_id:
        await check_resource_access(user_id, org_id, data.parent_id, "edit")

    folder = await document_service.create_folder(
        org_id=UUID(org_id) if org_id else None,
        owner_id=user_id,
        name=data.name,
        parent_id=data.parent_id,
        branch_id=data.branch_id,
        description=data.description
    )
    return folder


@router.get("/folders", response_model=FolderContents)
async def list_root_contents(
    org_context: dict = Depends(get_current_org_context),
    user_id: UUID = Depends(get_current_user_id)
):
    """List root contents (Branches or Personal Root)."""
    org_id = org_context.get("org_id")
    org_uuid = UUID(org_id) if org_id else None

    if not org_uuid:
        # Personal account - list root folders/files
        contents = await document_service.get_folder_contents(org_id=None, owner_id=user_id)
        return {"folder": None, **contents}

    branches = await document_service.get_branches(org_uuid)
    # Map branches to FolderResponse format to simulate root folders
    branch_folders = []
    for b in branches:
        branch_folders.append(FolderResponse(
            id=b["id"],
            name=b["name"],
            node_type=NodeType.BRANCH,
            parent_id=None,
            branch_id=None,
            description=None,
            owner_id=UUID(b["owner_id"]),
            created_at=b["created_at"],
            updated_at=b["updated_at"],
            children_count=0
        ))

    return {
        "folder": None,
        "folders": branch_folders,
        "documents": []
    }


@router.get("/folders/{folder_id}", response_model=FolderContents)
async def get_folder_contents(
    folder_id: UUID,
    type: str = Query("folder", regex="^(folder|branch)$"),
    org_context: dict = Depends(get_current_org_context),
    user_id: UUID = Depends(get_current_user_id)
):
    """Get folder (or branch) and its contents."""
    org_id = org_context.get("org_id")
    org_uuid = UUID(org_id) if org_id else None

    if type == "branch":
        if not org_uuid:
             raise HTTPException(status_code=400, detail="Personal accounts do not have branches")

        # Get contents of the branch root (filtered by user permissions)
        contents = await document_service.get_folder_contents(
            org_uuid, folder_id=None, branch_id=folder_id, user_id=user_id
        )
        return {"folder": None, **contents}
    else:
        # Standard folder
        folder = await document_service.get_folder(folder_id, org_uuid)
        if not folder:
             raise HTTPException(status_code=404, detail="Folder not found")
        contents = await document_service.get_folder_contents(
            org_uuid, folder_id=folder_id, owner_id=user_id, user_id=user_id
        )
        return {"folder": folder, **contents}


@router.put("/folders/{folder_id}", response_model=FolderResponse)
async def update_folder(
    folder_id: UUID,
    data: FolderUpdate,
    org_context: dict = Depends(get_current_org_context),
    user_id: UUID = Depends(get_current_user_id)
):
    """Update folder."""
    org_id = org_context.get("org_id")
    
    # Check permissions
    await check_general_permission(user_id, org_id, "edit")
    await check_resource_access(user_id, org_id, folder_id, "edit")

    updates = data.model_dump(exclude_none=True)
    return await document_service.update_document(folder_id, UUID(org_id) if org_id else None, **updates)


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: UUID,
    org_context: dict = Depends(get_current_org_context),
    user_id: UUID = Depends(get_current_user_id)
):
    """Delete folder."""
    org_id = org_context.get("org_id")
    
    # Check permissions
    await check_general_permission(user_id, org_id, "delete")
    await check_resource_access(user_id, org_id, folder_id, "edit")

    await document_service.delete_document(folder_id, UUID(org_id) if org_id else None)
    return {"success": True}


