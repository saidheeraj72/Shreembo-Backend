from typing import Optional, List
from uuid import UUID, uuid4
from urllib.parse import unquote_plus
from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File, Form

from src.core.dependencies import get_current_user_id, get_current_org_context
from src.documents.service import document_service
from src.models.document import DocumentUploadInit, DocumentUploadComplete, UploadInitResponse, DocumentResponse
from src.api.v1.document_routes.common import check_resource_access, check_general_permission

router = APIRouter()

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

    # Decode URL-encoded filename (e.g. "My+Document.pdf" -> "My Document.pdf")
    decoded_filename = unquote_plus(filename)

    return await document_service.complete_upload(
        org_id=UUID(org_id) if org_id else None,
        owner_id=user_id,
        upload_id=data.upload_id,
        s3_key=data.s3_key,
        filename=decoded_filename,
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


