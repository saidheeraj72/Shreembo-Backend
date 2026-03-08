"""Chat API router composition."""
from fastapi import APIRouter

from src.api.v1.chat_routes import sessions, messages, session_documents, usage, websocket

router = APIRouter(tags=["chat"])
router.include_router(sessions.router)
router.include_router(messages.router)
router.include_router(session_documents.router)
router.include_router(usage.router)
router.include_router(websocket.router)
