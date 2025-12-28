"""Document and folder API routes."""
from typing import Optional, List
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query, HTTPException, UploadFile, File, Form

from src.core.websocket import ws_manager
from src.core.dependencies import get_current_user_id, get_current_org_context
from src.services.document_service import document_service
from src.services.embedding_service import embedding_service
from src.models.document import (
    FolderCreate, FolderUpdate, DocumentUploadInit, DocumentUploadComplete,
    DocumentUpdate, DocumentMove, DocumentReplicate, DocumentResponse, FolderResponse,
    FolderContents, UploadInitResponse, SearchResult, NodeType
)

router = APIRouter()


# Folders
@router.post("/folders", response_model=FolderResponse)
async def create_folder(
    data: FolderCreate,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Create a new folder."""
    folder = await document_service.create_folder(
        org_id=UUID(org_context["org_id"]),
        owner_id=user_id,
        name=data.name,
        parent_id=data.parent_id,
        branch_id=data.branch_id,
        description=data.description
    )
    return folder


@router.get("/folders", response_model=FolderContents)
async def list_root_contents(
    org_context: dict = Depends(get_current_org_context)
):
    """List root contents (Branches)."""
    branches = await document_service.get_branches(UUID(org_context["org_id"]))
    # Map branches to FolderResponse format to simulate root folders
    branch_folders = []
    for b in branches:
        # Construct a FolderResponse-compatible dict
        # Ensure 'id' is preserved as UUID
        branch_folders.append(FolderResponse(
            id=b["id"],
            name=b["name"],
            node_type=NodeType.BRANCH,
            parent_id=None,
            branch_id=None,
            description=None,
            owner_id=UUID(b["owner_id"]), # Placeholder owner
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
    org_context: dict = Depends(get_current_org_context)
):
    """Get folder (or branch) and its contents."""
    org_id = UUID(org_context["org_id"])
    
    if type == "branch":
        # Get contents of the branch root
        contents = await document_service.get_folder_contents(org_id, folder_id=None, branch_id=folder_id)
        # We don't have a "Folder" object for the branch here easily available without querying branches table
        # But FolderContents.folder is optional, so we can return None or construct one.
        # For UI consistency, it's better if we return the branch details as 'folder' metadata if possible.
        # But 'document_service.get_folder' looks at storage_nodes. 
        # We'll return None for 'folder' metadata for now, UI should handle it via breadcrumbs.
        return {"folder": None, **contents}
    else:
        # Standard folder
        folder = await document_service.get_folder(folder_id, org_id)
        if not folder:
             raise HTTPException(status_code=404, detail="Folder not found")
        contents = await document_service.get_folder_contents(org_id, folder_id=folder_id)
        return {"folder": folder, **contents}


@router.put("/folders/{folder_id}", response_model=FolderResponse)
async def update_folder(
    folder_id: UUID,
    data: FolderUpdate,
    org_context: dict = Depends(get_current_org_context)
):
    """Update folder."""
    updates = data.model_dump(exclude_none=True)
    return await document_service.update_document(folder_id, UUID(org_context["org_id"]), **updates)


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: UUID,
    org_context: dict = Depends(get_current_org_context)
):
    """Delete folder."""
    await document_service.delete_document(folder_id, UUID(org_context["org_id"]))
    return {"success": True}


# Documents
@router.post("/upload/init", response_model=UploadInitResponse)
async def init_upload(
    data: DocumentUploadInit,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Initialize document upload, returns presigned URL."""
    result = await document_service.init_upload(
        org_id=UUID(org_context["org_id"]),
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
    return await document_service.complete_upload(
        org_id=UUID(org_context["org_id"]),
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
    # Validate file type
    if not file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail="File must be a ZIP archive")

    # Read file content
    zip_bytes = await file.read()
    upload_id = str(uuid4())

    # Process the ZIP
    result = await document_service.process_zip_upload(
        zip_bytes=zip_bytes,
        org_id=UUID(org_context["org_id"]),
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
    org_context: dict = Depends(get_current_org_context)
):
    """Get document details."""
    return await document_service.get_document(doc_id, UUID(org_context["org_id"]))


@router.put("/documents/{doc_id}", response_model=DocumentResponse)
async def update_document(
    doc_id: UUID,
    data: DocumentUpdate,
    org_context: dict = Depends(get_current_org_context)
):
    """Update document."""
    updates = data.model_dump(exclude_none=True)
    return await document_service.update_document(doc_id, UUID(org_context["org_id"]), **updates)


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: UUID,
    org_context: dict = Depends(get_current_org_context)
):
    """Delete document."""
    await document_service.delete_document(doc_id, UUID(org_context["org_id"]))
    return {"success": True}


@router.post("/documents/{doc_id}/move", response_model=DocumentResponse)
async def move_document(
    doc_id: UUID,
    data: DocumentMove,
    org_context: dict = Depends(get_current_org_context)
):
    """Move document to another folder."""
    return await document_service.move_document(doc_id, UUID(org_context["org_id"]), data.target_folder_id)


@router.post("/documents/{doc_id}/replicate", response_model=DocumentResponse)
async def replicate_document(
    doc_id: UUID,
    data: DocumentReplicate,
    org_context: dict = Depends(get_current_org_context)
):
    """Replicate document to another branch (copies file, metadata, and embeddings)."""
    result = await document_service.replicate_document(
        doc_id, UUID(org_context["org_id"]), data.target_branch_id
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
    url = await document_service.get_download_url(doc_id, UUID(org_context["org_id"]))
    return {"download_url": url}


@router.get("/documents/{doc_id}/view")
async def get_view_url(
    doc_id: UUID,
    org_context: dict = Depends(get_current_org_context)
):
    """Get presigned URL for viewing document in browser."""
    url = await document_service.get_view_url(doc_id, UUID(org_context["org_id"]))
    if not url:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"view_url": url}


# Search
@router.get("/search", response_model=List[SearchResult])
async def search_documents(
    q: str = Query(..., min_length=1),
    top_k: int = Query(10, ge=1, le=50),
    folder_id: Optional[UUID] = Query(None),
    org_context: dict = Depends(get_current_org_context)
):
    """Search documents using semantic search."""
    return await embedding_service.search(q, UUID(org_context["org_id"]), top_k, folder_id)


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