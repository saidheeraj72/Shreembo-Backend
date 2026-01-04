"""Document and folder API routes."""
from typing import Optional, List
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query, HTTPException, UploadFile, File, Form

from src.core.websocket import ws_manager
from src.core.dependencies import get_current_user_id, get_current_org_context
from src.services.document_service import document_service
from src.services.embedding_service import embedding_service
from src.services.permission_service import permission_service
from src.models.document import (
    FolderCreate, FolderUpdate, DocumentUploadInit, DocumentUploadComplete,
    DocumentUpdate, DocumentMove, DocumentReplicate, DocumentResponse, FolderResponse,
    FolderContents, UploadInitResponse, SearchResult, NodeType
)

router = APIRouter()


async def check_resource_access(
    user_id: UUID, 
    org_id: Optional[str], 
    node_id: Optional[UUID], 
    required_level: str = "edit"
):
    """Helper to check resource access for org users."""
    if not org_id:
        return # Personal users have full access
        
    has_access = await permission_service.check_folder_permission(
        user_id=user_id,
        folder_id=node_id,
        org_id=UUID(org_id),
        required_level=required_level
    )
    if not has_access:
        raise HTTPException(
            status_code=403, 
            detail=f"You do not have {required_level} permission on this folder/document"
        )

async def check_general_permission(
    user_id: UUID, 
    org_id: Optional[str], 
    action: str
):
    """Helper to check general document permissions."""
    if not org_id:
        return
        
    has_perm = await permission_service.check_permission(
        user_id=user_id,
        org_id=UUID(org_id),
        module="documents",
        action=action
    )
    if not has_perm:
        raise HTTPException(
            status_code=403,
            detail=f"You do not have permission to {action} documents"
        )


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


# Documents
@router.post("/upload/init", response_model=UploadInitResponse)
async def init_upload(
    data: DocumentUploadInit,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Initialize document upload, returns presigned URL."""
    org_id = org_context.get("org_id")
    
    # Check permissions
    await check_general_permission(user_id, org_id, "create")
    
    if data.parent_id:
        await check_resource_access(user_id, org_id, data.parent_id, "edit")

    result = await document_service.init_upload(
        org_id=UUID(org_id) if org_id else None,
        owner_id=user_id,
        filename=data.filename,
        content_type=data.content_type,
        size_bytes=data.size_bytes,
        parent_id=data.parent_id,
        branch_id=data.branch_id
    )
    return result


@router.post("/upload/complete", response_model=DocumentResponse)
async def complete_upload(
    data: DocumentUploadComplete,
    filename: str = Query(...),
    content_type: str = Query(...),
    size_bytes: int = Query(...),
    parent_id: Optional[UUID] = Query(None),
    branch_id: Optional[UUID] = Query(None),
    description: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Complete document upload after S3 upload is done."""
    org_id = org_context.get("org_id")
    
    if parent_id:
         await check_resource_access(user_id, org_id, parent_id, "edit")

    return await document_service.complete_upload(
        org_id=UUID(org_id) if org_id else None,
        owner_id=user_id,
        upload_id=data.upload_id,
        s3_key=data.s3_key,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        parent_id=parent_id,
        branch_id=branch_id,
        description=description,
        tags=tags
    )


@router.post("/upload/zip")
async def upload_zip(
    file: UploadFile = File(...),
    branch_id: str = Form(...),
    parent_id: Optional[str] = Form(None),
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Upload a ZIP file and extract its contents, creating folder structure."""
    org_id = org_context.get("org_id")
    
    # Check permissions
    await check_general_permission(user_id, org_id, "create")
    
    if parent_id:
        await check_resource_access(user_id, org_id, UUID(parent_id), "edit")

    # Validate file type
    if not file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail="File must be a ZIP archive")

    # Read file content
    zip_bytes = await file.read()
    upload_id = str(uuid4())

    # Process the ZIP
    result = await document_service.process_zip_upload(
        zip_bytes=zip_bytes,
        org_id=UUID(org_id) if org_id else None,
        owner_id=user_id,
        branch_id=UUID(branch_id),
        parent_id=UUID(parent_id) if parent_id else None,
        user_id=str(user_id),
        upload_id=upload_id
    )

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to process ZIP"))

    return {
        "success": True,
        "upload_id": upload_id,
        "folders_created": result["folders_created"],
        "files_created": result["files_created"],
        "errors": result["errors"]
    }


@router.get("/documents/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: UUID,
    org_context: dict = Depends(get_current_org_context),
    user_id: UUID = Depends(get_current_user_id)
):
    """Get document details."""
    org_id = org_context.get("org_id")
    
    # Check read access
    await check_general_permission(user_id, org_id, "view")
    await check_resource_access(user_id, org_id, doc_id, "view")
    
    return await document_service.get_document(doc_id, UUID(org_id) if org_id else None)


@router.put("/documents/{doc_id}", response_model=DocumentResponse)
async def update_document(
    doc_id: UUID,
    data: DocumentUpdate,
    org_context: dict = Depends(get_current_org_context),
    user_id: UUID = Depends(get_current_user_id)
):
    """Update document."""
    org_id = org_context.get("org_id")
    
    # Check permissions
    await check_general_permission(user_id, org_id, "edit")
    await check_resource_access(user_id, org_id, doc_id, "edit")

    updates = data.model_dump(exclude_none=True)
    return await document_service.update_document(doc_id, UUID(org_id) if org_id else None, **updates)


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: UUID,
    org_context: dict = Depends(get_current_org_context),
    user_id: UUID = Depends(get_current_user_id)
):
    """Delete document."""
    org_id = org_context.get("org_id")
    
    # Check permissions
    await check_general_permission(user_id, org_id, "delete")
    await check_resource_access(user_id, org_id, doc_id, "edit")

    await document_service.delete_document(doc_id, UUID(org_id) if org_id else None)
    return {"success": True}


@router.post("/documents/{doc_id}/move", response_model=DocumentResponse)
async def move_document(
    doc_id: UUID,
    data: DocumentMove,
    org_context: dict = Depends(get_current_org_context)
):
    """Move document to another folder."""
    org_id = org_context.get("org_id")
    return await document_service.move_document(doc_id, UUID(org_id) if org_id else None, data.target_folder_id)


@router.post("/documents/{doc_id}/replicate", response_model=DocumentResponse)
async def replicate_document(
    doc_id: UUID,
    data: DocumentReplicate,
    org_context: dict = Depends(get_current_org_context)
):
    """Replicate document to another branch (copies file, metadata, and embeddings)."""
    org_id = org_context.get("org_id")
    result = await document_service.replicate_document(
        doc_id, UUID(org_id) if org_id else None, data.target_branch_id
    )
    if not result:
        raise HTTPException(status_code=404, detail="Document not found")
    return result


@router.get("/documents/{doc_id}/download")
async def get_download_url(
    doc_id: UUID,
    org_context: dict = Depends(get_current_org_context)
):
    """Get presigned download URL."""
    org_id = org_context.get("org_id")
    url = await document_service.get_download_url(doc_id, UUID(org_id) if org_id else None)
    return {"download_url": url}


@router.get("/documents/{doc_id}/view")
async def get_view_url(
    doc_id: UUID,
    org_context: dict = Depends(get_current_org_context)
):
    """Get presigned URL for viewing document in browser."""
    org_id = org_context.get("org_id")
    url = await document_service.get_view_url(doc_id, UUID(org_id) if org_id else None)
    if not url:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"view_url": url}


# Search
@router.get("/search", response_model=List[SearchResult])
async def search_documents(
    q: str = Query(..., min_length=1),
    top_k: int = Query(10, ge=1, le=50),
    folder_id: Optional[UUID] = Query(None),
    org_context: dict = Depends(get_current_org_context),
    user_id: UUID = Depends(get_current_user_id) # Inject user_id
):
    """Search documents using semantic search."""
    org_id = org_context.get("org_id")
    return await embedding_service.search(q, UUID(org_id) if org_id else None, user_id, top_k, folder_id)


# WebSocket for upload progress
@router.websocket("/ws/{user_id}")
async def websocket_upload_progress(websocket: WebSocket, user_id: str):
    """WebSocket for real-time upload progress."""
    await ws_manager.connect(websocket, user_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, user_id)