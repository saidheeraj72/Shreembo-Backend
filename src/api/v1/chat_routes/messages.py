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

# ============== Message Endpoints (REST fallback) ==============

@router.get("/sessions/{session_id}/messages", response_model=List[ChatMessageResponse])
async def get_messages(
    session_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    before_id: Optional[UUID] = Query(None),
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Get messages for a session (REST endpoint)."""
    org_id = org_context.get("org_id")
    # Verify access
    session = await chat_service.get_session(
        session_id=session_id,
        user_id=user_id,
        org_id=UUID(org_id) if org_id else None
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = await chat_service.get_session_messages(session_id, limit, before_id)
    return messages


@router.post("/sessions/{session_id}/messages", response_model=ChatMessageResponse)
async def send_message(
    session_id: UUID,
    data: ChatMessageRequest,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """
    Send a message (non-streaming REST endpoint).
    For streaming, use the WebSocket endpoint.
    """
    org_id = org_context.get("org_id")
    session = await chat_service.get_session(
        session_id=session_id,
        user_id=user_id,
        org_id=UUID(org_id) if org_id else None
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Save user message
    await chat_service.add_message(
        session_id=session_id,
        role="user",
        content=data.content
    )

    # Update title if first message
    if session["message_count"] == 0:
        new_title = await chat_service.generate_session_title(data.content)
        await chat_service.update_session(session_id, user_id, title=new_title)

    # Generate response (non-streaming)
    rag_enabled = data.rag_enabled if data.rag_enabled is not None else session["rag_enabled"]
    web_enabled = data.web_search_enabled if data.web_search_enabled is not None else session["web_search_enabled"]

    try:
        result = await rag_service.generate_response_non_streaming(
            user_message=data.content,
            session_id=session_id,
            user_id=user_id,
            org_id=UUID(org_id) if org_id else None,
            rag_enabled=rag_enabled,
            web_search_enabled=web_enabled
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Save assistant message
    message = await chat_service.add_message(
        session_id=session_id,
        role="assistant",
        content=result["content"],
        rag_context=result.get("rag_results"),
        web_search_results=result.get("web_results"),
        sources=result.get("sources"),
        prompt_tokens=result.get("prompt_tokens", 0),
        completion_tokens=result.get("completion_tokens", 0),
        total_tokens=result.get("total_tokens", 0)
    )

    return message


