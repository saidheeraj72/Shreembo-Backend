"""Authentication API router composition."""
from fastapi import APIRouter

from src.api.v1.auth_routes import account, invitations, organizations

router = APIRouter()
router.include_router(account.router)
router.include_router(invitations.router)
router.include_router(organizations.router)
