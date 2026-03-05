"""Chat API routes."""
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
from src.services.chat_service import chat_service
from src.services.rag_service import rag_service
from src.services.session_document_service import session_document_service
from src.services.token_usage_service import token_usage_service
from src.services.permission_service import permission_service
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
        web_search_enabled=data.web_search_enabled
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


# ============== Token Usage ==============

@router.get("/usage", response_model=List[TokenUsageResponse])
async def get_user_usage(
    org_usage: bool = Query(False, description="Get org usage instead of personal"),
    limit: int = Query(12, ge=1, le=24),
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Get current user's token usage."""
    org_id = org_context.get("org_id")

    if org_usage and org_id:
        usage = await token_usage_service.get_user_usage(
            user_id=user_id,
            org_id=UUID(org_id),
            limit=limit
        )
    else:
        usage = await token_usage_service.get_user_usage(
            user_id=user_id,
            org_id=None,
            limit=limit
        )

    return usage


@router.get("/usage/all", response_model=List[TokenUsageResponse])
async def get_all_user_usage(
    limit: int = Query(12, ge=1, le=24),
    user_id: UUID = Depends(get_current_user_id)
):
    """Get all token usage for current user (both org and personal)."""
    usage = await token_usage_service.get_user_all_usage(user_id, limit)
    return usage


@router.get("/usage/org", response_model=TokenUsageSummary)
async def get_org_usage(
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Get org-wide token usage (admin only)."""
    org_id = org_context.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="Organization required")

    # Check if user is admin
    is_admin = await permission_service.is_admin_or_owner(user_id, UUID(org_id))
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    summary = await token_usage_service.get_org_usage_summary(UUID(org_id))

    return {
        "total_prompt_tokens": summary["total_prompt_tokens"],
        "total_completion_tokens": summary["total_completion_tokens"],
        "total_tokens": summary["total_tokens"],
        "total_chat_requests": summary["total_chat_requests"],
        "total_rag_requests": summary["total_rag_requests"],
        "total_web_search_requests": summary["total_web_search_requests"],
        "periods": summary.get("users", [])
    }


# ============== WebSocket Endpoint ==============

@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for real-time chat streaming.

    Authentication: Pass token as query parameter.

    Message types (client -> server):
    - send_message: {type, session_id, content, rag_enabled?, web_search_enabled?}
    - stop_generation: {type, session_id}
    - join_session: {type, session_id}
    - leave_session: {type, session_id}

    Message types (server -> client):
    - stream_start: {type, session_id, message_id}
    - stream_chunk: {type, session_id, content}
    - stream_end: {type, session_id, message_id, content, sources, token_usage}
    - stream_error: {type, session_id, error}
    - rag_context: {type, session_id, sources}
    - web_search: {type, session_id, results}
    """
    # Get token from query params
    token = websocket.query_params.get("token")

    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    # Verify token
    payload = verify_supabase_jwt(token)
    if not payload:
        await websocket.close(code=4001, reason="Invalid token")
        return

    user_id = UUID(payload["sub"])

    # Get org context
    profile = db.admin.table("profiles").select("org_id").eq(
        "id", str(user_id)
    ).maybe_single().execute()

    org_id = None
    if profile and profile.data and profile.data.get("org_id"):
        org_id = UUID(profile.data["org_id"])

    # Connect
    await chat_ws_manager.connect(websocket, str(user_id))

    try:
        while True:
            data = await websocket.receive_json()
            await chat_ws_manager.handle_message(
                websocket=websocket,
                user_id=user_id,
                org_id=org_id,
                data=data
            )
    except WebSocketDisconnect:
        chat_ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket error: %s", e)
        chat_ws_manager.disconnect(websocket)
