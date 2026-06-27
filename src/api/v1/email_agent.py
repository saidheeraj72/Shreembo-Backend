"""
Email Agent API router — composes OAuth connect flow + mailbox operations.
"""
from fastapi import APIRouter

from src.api.v1.email_agent_routes import mailbox, oauth

router = APIRouter()
router.include_router(oauth.router)
router.include_router(mailbox.router)
