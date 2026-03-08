from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException

from src.core.dependencies import get_current_user_id, get_current_org_context
from src.documents.service import document_service
from src.models.document import DocumentUpdate, DocumentMove, DocumentReplicate, DocumentResponse
from src.api.v1.document_routes.common import check_resource_access, check_general_permission

router = APIRouter()

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


