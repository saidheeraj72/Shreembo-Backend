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

# ============== Session Endpoints ==============

@router.post("/sessions", response_model=ChatSessionResponse)
async def create_session(
    data: ChatSessionCreate,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Create a new chat session."""
    org_id = org_context.get("org_id")
    session = await chat_service.create_session(
        user_id=user_id,
        org_id=UUID(org_id) if org_id else None,
        title=data.title,
        rag_enabled=data.rag_enabled,
        web_search_enabled=data.web_search_enabled,
        context_nodes=[n.model_dump() for n in data.context_nodes] if data.context_nodes else None
    )
    return session


@router.get("/sessions", response_model=List[ChatSessionResponse])
async def list_sessions(
    include_shared: bool = Query(True),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """List user's chat sessions."""
    org_id = org_context.get("org_id")
    sessions = await chat_service.get_user_sessions(
        user_id=user_id,
        org_id=UUID(org_id) if org_id else None,
        include_shared=include_shared,
        limit=limit,
        offset=offset
    )
    return sessions


@router.get("/sessions/{session_id}", response_model=ChatSessionWithMessages)
async def get_session(
    session_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Get a chat session with messages."""
    org_id = org_context.get("org_id")
    session = await chat_service.get_session(
        session_id=session_id,
        user_id=user_id,
        org_id=UUID(org_id) if org_id else None
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = await chat_service.get_session_messages(session_id)
    return {**session, "messages": messages}


@router.patch("/sessions/{session_id}", response_model=ChatSessionResponse)
async def update_session(
    session_id: UUID,
    data: ChatSessionUpdate,
    user_id: UUID = Depends(get_current_user_id)
):
    """Update chat session settings."""
    updates = data.model_dump(exclude_none=True)
    session = await chat_service.update_session(session_id, user_id, **updates)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: UUID,
    user_id: UUID = Depends(get_current_user_id)
):
    """Delete a chat session."""
    success = await chat_service.delete_session(session_id, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"success": True}


@router.post("/sessions/{session_id}/share", response_model=ChatSessionResponse)
async def share_session(
    session_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Share session with organization members."""
    org_id = org_context.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="Organization required for sharing")

    session = await chat_service.share_session(session_id, user_id, UUID(org_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/sessions/{session_id}/unshare", response_model=ChatSessionResponse)
async def unshare_session(
    session_id: UUID,
    user_id: UUID = Depends(get_current_user_id)
):
    """Unshare a session."""
    session = await chat_service.unshare_session(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


