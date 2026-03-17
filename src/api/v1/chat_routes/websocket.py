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
