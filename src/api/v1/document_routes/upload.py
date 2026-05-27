from typing import Optional, List
from uuid import UUID, uuid4
from urllib.parse import unquote_plus
from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File, Form

from src.core.dependencies import get_current_user_id, get_current_org_context
from src.documents.service import document_service
from src.models.document import DocumentUploadInit, DocumentUploadComplete, UploadInitResponse, DocumentResponse
from src.api.v1.document_routes.common import check_resource_access, check_general_permission

router = APIRouter()


@router.post("/upload/direct", response_model=DocumentResponse)
async def direct_upload(
    file: UploadFile = File(...),
    parent_id: Optional[str] = Form(None),
    branch_id: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Upload a file directly through the backend to Supabase Storage."""
    org_id = org_context.get("org_id")

    # Check permissions
    await check_general_permission(user_id, org_id, "create")

    parent_uuid = UUID(parent_id) if parent_id else None
    branch_uuid = UUID(branch_id) if branch_id else None

    if parent_uuid:
        await check_resource_access(user_id, org_id, parent_uuid, "edit")

    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"

    return await document_service.direct_upload(
        org_id=UUID(org_id) if org_id else None,
        owner_id=user_id,
        filename=file.filename,
        content_type=content_type,
        size_bytes=len(file_bytes),
        file_bytes=file_bytes,
        parent_id=parent_uuid,
        branch_id=branch_uuid,
        description=description,
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


