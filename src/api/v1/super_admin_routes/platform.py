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

@router.get("/users")
async def list_all_users(
    _: dict = Depends(require_super_admin),
):
    """
    List all users in the platform.

    **Requires:** Super admin access

    Returns list of all users with their organization membership.
    """
    from src.core.database import db

    response = (
        db.admin.table("profiles")
        .select("*, organizations(name)")
        .order("created_at", desc=True)
        .execute()
    )

    return {
        "total": len(response.data),
        "users": response.data,
    }


@router.get("/audit-logs")
async def get_platform_audit_logs(
    _: dict = Depends(require_super_admin),
    limit: int = 100,
):
    """
    Get platform-wide audit logs.

    **Requires:** Super admin access

    Query Parameters:
    - **limit**: Maximum number of logs to return (default: 100)
    """
    from src.core.database import db

    response = (
        db.admin.table("audit_logs")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )

    return {
        "total": len(response.data),
        "logs": response.data,
    }


@router.get("/stats")
async def get_platform_statistics(
    _: dict = Depends(require_super_admin),
):
    """
    Get comprehensive platform statistics.

    **Requires:** Super admin access

    Returns detailed statistics about the entire platform.
    """
    from src.core.database import db

    # Organizations by plan type
    orgs_by_plan = (
        db.admin.table("organizations")
        .select("plan_type", count="exact")
        .execute()
    )

    # Users by account type
    users_by_type = (
        db.admin.table("profiles")
        .select("account_type", count="exact")
        .execute()
    )

    # Active vs inactive users
    active_users = (
        db.admin.table("profiles")
        .select("id", count="exact")
        .eq("status", "active")
        .execute()
    )

    # Storage usage
    storage_usage = (
        db.admin.table("storage_nodes")
        .select("file_size")
        .eq("node_type", "file")
        .eq("status", "active")
        .execute()
    )

    total_storage_bytes = sum(
        node.get("file_size", 0) or 0 for node in storage_usage.data
    )
    total_storage_gb = round(total_storage_bytes / (1024**3), 2)

    return {
        "organizations": {
            "total": orgs_by_plan.count or 0,
            "by_plan": {},  # TODO: Group by plan type
        },
        "users": {
            "total": users_by_type.count or 0,
            "active": active_users.count or 0,
            "by_type": {},  # TODO: Group by account type
        },
        "storage": {
            "total_files": len(storage_usage.data),
            "total_storage_gb": total_storage_gb,
            "total_storage_bytes": total_storage_bytes,
        },
    }


@router.get("/organization-usage")
async def get_organization_usage(
    _: dict = Depends(require_super_admin),
):
    """
    Get usage statistics for all organizations.

    **Requires:** Super admin access

    Returns usage statistics (tokens, chat, RAG) for each organization.
    """
    from src.core.database import db

    # Get all organizations
    orgs_response = (
        db.admin.table("organizations")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )

    organizations_with_usage = []

    for org in orgs_response.data:
        org_id = UUID(org["id"])

        # Get usage stats for this organization
        try:
            usage_stats = await limit_service.get_usage_stats("organization", org_id)
        except Exception:
            # If stats aren't available, use defaults
            usage_stats = None

        organizations_with_usage.append({
            **org,
            "monthly_tokens_used": usage_stats.monthly_tokens_used if usage_stats else 0,
            "monthly_tokens_limit": usage_stats.monthly_tokens_limit if usage_stats else 1000000,
            "daily_chat_requests_used": usage_stats.daily_chat_requests_used if usage_stats else 0,
            "daily_chat_requests_limit": usage_stats.daily_chat_requests_limit if usage_stats else 1000,
            "daily_rag_requests_used": usage_stats.daily_rag_requests_used if usage_stats else 0,
            "daily_rag_requests_limit": usage_stats.daily_rag_requests_limit if usage_stats else 500,
        })

    return {
        "total": len(organizations_with_usage),
        "organizations": organizations_with_usage,
    }

