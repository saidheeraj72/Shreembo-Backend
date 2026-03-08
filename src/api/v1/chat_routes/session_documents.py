from typing import Optional, List
from uuid import UUID
from datetime import date
import logging
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query, HTTPException, BackgroundTasks

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


@router.post("/sessions/{session_id}/documents/upload/init", response_model=SessionDocumentUploadResponse)
async def init_document_upload(
    session_id: UUID,
    data: SessionDocumentUploadInit,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Initialize document upload for a chat session."""
    org_id = org_context.get("org_id")
    session = await chat_service.get_session(
        session_id=session_id,
        user_id=user_id,
        org_id=UUID(org_id) if org_id else None
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Only session owner can upload
    if str(session["user_id"]) != str(user_id):
        raise HTTPException(status_code=403, detail="Only session owner can upload documents")

    result = await session_document_service.init_upload(
        session_id=session_id,
        user_id=user_id,
        org_id=UUID(org_id) if org_id else None,
        filename=data.filename,
        content_type=data.content_type,
        size_bytes=data.size_bytes
    )
    return result


@router.post("/sessions/{session_id}/documents/complete")
async def complete_document_upload(
    session_id: UUID,
    data: SessionDocumentUploadComplete,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Complete document upload and trigger embedding."""
    org_id = org_context.get("org_id")
    try:
        result = await session_document_service.complete_upload(
            session_id=session_id,
            user_id=user_id,
            org_id=UUID(org_id) if org_id else None,
            s3_key=data.s3_key,
            filename=data.filename,
            file_type=data.file_type,
            file_size=data.file_size,
            mime_type=data.mime_type,
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


