"""Bounce Board — AI executive board for business problems (aggregator router)."""
from fastapi import APIRouter

from src.api.v1.bounce_board_routes import knowledge, sessions, ws

router = APIRouter()
router.include_router(sessions.router)
router.include_router(knowledge.router)
router.include_router(ws.router)
