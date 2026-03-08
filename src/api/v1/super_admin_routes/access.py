from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
import logging

from src.models.super_admin import SuperAdmin, SuperAdminVerifyResponse
from src.models.user import UserProfile
from src.super_admin.service import super_admin_service
from src.access.limit import limit_service
from src.api.deps.permissions import require_super_admin
from src.core.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/verify", response_model=SuperAdminVerifyResponse)
async def verify_super_admin_access(
    user: dict = Depends(get_current_user),
):
    """
    Verify if current user is a super admin.

    This endpoint can be called by any authenticated user to check
    if they have super admin privileges.

    Returns:
    - **is_super_admin**: Boolean indicating super admin status
    - **email**: User's email address
    """
    email = user.get("email")
    is_super_admin = await super_admin_service.verify_super_admin(email)

    return SuperAdminVerifyResponse(
        is_super_admin=is_super_admin,
        email=email,
    )


@router.get("/list", response_model=List[SuperAdmin])
async def list_super_admins(
    _: dict = Depends(require_super_admin),
):
    """
    List all super admins.

    **Requires:** Super admin access

    Returns list of all super admins with their details.
    """
    super_admins = await super_admin_service.list_super_admins()
    return super_admins

