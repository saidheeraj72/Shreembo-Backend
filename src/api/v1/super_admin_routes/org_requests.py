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

@router.get("/organization-requests")
async def list_organization_requests(
    status: str | None = None,
    _: dict = Depends(require_super_admin),
):
    """
    List all organization requests.

    **Requires:** Super admin access

    Query Parameters:
    - **status**: Filter by status (pending, approved, rejected)

    Returns list of organization requests.
    """
    from src.core.database import db

    query = db.admin.table("organization_requests").select("*")

    if status:
        query = query.eq("status", status)

    response = query.order("created_at", desc=True).execute()

    return {
        "total": len(response.data),
        "requests": response.data,
    }


