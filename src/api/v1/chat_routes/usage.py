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


