from typing import Optional, List
from uuid import UUID
from datetime import date
import logging
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query, HTTPException, BackgroundTasks, UploadFile, File, Form

logger = logging.getLogger(__name__)

from src.core.dependencies import get_current_user_id, get_current_org_context
from src.core.chat_websocket import chat_ws_manager
from src.core.security import verify_supabase_jwt
from src.core.database import db
from src.chat.service import chat_service
from src.llm.rag import rag_service
from src.chat.session_document import session_document_service
from src.llm.token_usage import token_usage_service
from src.access.permission import permission_service
from src.models.chat import (
    ChatSessionCreate, ChatSessionUpdate, ChatMessageRequest,
    ChatSessionResponse, ChatSessionWithMessages, ChatMessageResponse,
    SessionDocumentResponse, SessionDocumentUploadInit, SessionDocumentUploadComplete,
    SessionDocumentUploadResponse, TokenUsageResponse, TokenUsageSummary
)

router = APIRouter(tags=["chat"])

# ============== Session Documents ==============

@router.get("/sessions/{session_id}/documents", response_model=List[SessionDocumentResponse])
async def get_session_documents(
    session_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Get documents uploaded to a session."""
    org_id = org_context.get("org_id")
    session = await chat_service.get_session(
        session_id=session_id,
        user_id=user_id,
        org_id=UUID(org_id) if org_id else None
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    documents = await session_document_service.get_session_documents(session_id)
    return documents


@router.post("/sessions/{session_id}/documents/upload/direct")
async def direct_document_upload(
    session_id: UUID,
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Upload a document directly to a chat session via Supabase Storage."""
    org_id = org_context.get("org_id")
    session = await chat_service.get_session(
        session_id=session_id,
        user_id=user_id,
        org_id=UUID(org_id) if org_id else None
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if str(session["user_id"]) != str(user_id):
        raise HTTPException(status_code=403, detail="Only session owner can upload documents")

    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"

    # Upload to Supabase Storage and get metadata
    init_result = await session_document_service.init_upload(
        session_id=session_id,
        user_id=user_id,
        org_id=UUID(org_id) if org_id else None,
        filename=file.filename,
        content_type=content_type,
        size_bytes=len(file_bytes),
        file_bytes=file_bytes
    )

    # Complete upload and trigger embedding
    try:
        result = await session_document_service.complete_upload(
            session_id=session_id,
            user_id=user_id,
            org_id=UUID(org_id) if org_id else None,
            s3_key=init_result["s3_key"],
            filename=file.filename,
            file_type=init_result["file_type"],
            file_size=len(file_bytes),
            mime_type=content_type,
            background_tasks=background_tasks
        )
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/documents/{session_doc_id}/reprocess")
async def reprocess_document(
    session_id: UUID,
    session_doc_id: UUID,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Reprocess a failed or stuck session document."""
    org_id = org_context.get("org_id")
    try:
        await session_document_service.reprocess_document(
            session_document_id=session_doc_id,
            user_id=user_id,
            org_id=UUID(org_id) if org_id else None,
            background_tasks=background_tasks
        )
        return {"success": True, "message": "Document reprocessing started"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


