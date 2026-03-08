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

@router.get("/dashboard")
async def get_super_admin_dashboard(
    user: dict = Depends(require_super_admin),
):
    """
    Get super admin dashboard data.

    **Requires:** Super admin access

    Returns:
    - Platform statistics
    - Recent activity
    - List of all organizations
    - System health
    """
    from src.core.database import db

    # Get organization count
    orgs_response = (
        db.admin.table("organizations")
        .select("id", count="exact")
        .execute()
    )

    # Get user count
    users_response = (
        db.admin.table("profiles")
        .select("id", count="exact")
        .execute()
    )

    # Get total files
    files_response = (
        db.admin.table("storage_nodes")
        .select("id", count="exact")
        .eq("node_type", "file")
        .execute()
    )

    # Get all organizations with member count
    all_orgs_response = (
        db.admin.table("organizations")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )

    # Get member counts for each organization
    organizations = []
    for org in all_orgs_response.data:
        # Count members for this org
        member_count_response = (
            db.admin.table("profiles")
            .select("id", count="exact")
            .eq("org_id", org["id"])
            .execute()
        )

        organizations.append({
            **org,
            "member_count": member_count_response.count or 0,
        })

    return {
        "statistics": {
            "total_organizations": orgs_response.count or 0,
            "total_users": users_response.count or 0,
            "total_files": files_response.count or 0,
        },
        "organizations": organizations,
        "super_admin": {
            "email": user.get("email"),
            "full_name": user.get("full_name"),
        },
    }


@router.get("/organizations")
async def list_all_organizations(
    _: dict = Depends(require_super_admin),
):
    """
    List all organizations in the platform.

    **Requires:** Super admin access

    Returns list of all organizations with statistics.
    """
    from src.core.database import db

    # Get all organizations with member count
    response = (
        db.admin.table("organizations")
        .select("*, organization_members(count)")
        .order("created_at", desc=True)
        .execute()
    )

    # Format the response
    organizations = []
    for org in response.data:
        member_count = 0
        if org.get("organization_members"):
            member_count = len(org["organization_members"])

        organizations.append({
            **org,
            "member_count": member_count,
        })

    return {
        "total": len(organizations),
        "organizations": organizations,
    }


