from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Body
from pydantic import BaseModel

from src.core.dependencies import get_current_user, get_current_org_context
from src.api.deps.permissions import require_permission
from src.audit.service import audit_service
from src.access.limit import limit_service

router = APIRouter()


class UserAccessUpdate(BaseModel):
    rag_enabled: Optional[bool] = None
    chat_enabled: Optional[bool] = None

# ==========================================
# USER USAGE & ACCESS CONTROL ENDPOINTS
# ==========================================

@router.get(
    "/user-usage",
    response_model=List[dict],
    dependencies=[Depends(require_permission("usage", "view"))],
)
async def get_user_usage(
    org_context: dict = Depends(get_current_org_context),
):
    """
    Get usage statistics and access settings for all users in the organization.

    **Requires:** usage.view permission

    Returns user details with usage stats, limits, and access control settings.
    """
    from src.core.database import db

    org_id = UUID(org_context["org_id"])

    # Get all organization members with user details
    members_result = db.admin.table("organization_members").select(
        "user_id, rag_enabled, chat_enabled"
    ).eq("org_id", str(org_id)).eq("status", "active").execute()

    if not members_result.data:
        return []

    user_usage_list = []

    for member in members_result.data:
        user_id = UUID(member["user_id"])

        # Get user profile
        profile_result = db.admin.table("profiles").select(
            "full_name, email"
        ).eq("id", str(user_id)).execute()

        if not profile_result.data:
            continue

        profile = profile_result.data[0]

        # Get usage stats
        try:
            usage_stats = await limit_service.get_usage_stats("user", user_id)
        except Exception:
            # If stats aren't available, use defaults
            usage_stats = None

        user_usage_list.append({
            "id": str(user_id),
            "full_name": profile.get("full_name", "Unknown"),
            "email": profile.get("email", ""),
            "monthly_tokens_used": usage_stats.monthly_tokens_used if usage_stats else 0,
            "monthly_tokens_limit": usage_stats.monthly_tokens_limit if usage_stats else 0,
            "daily_chat_requests_used": usage_stats.daily_chat_requests_used if usage_stats else 0,
            "daily_chat_requests_limit": usage_stats.daily_chat_requests_limit if usage_stats else 0,
            "daily_rag_requests_used": usage_stats.daily_rag_requests_used if usage_stats else 0,
            "daily_rag_requests_limit": usage_stats.daily_rag_requests_limit if usage_stats else 0,
            "rag_enabled": member.get("rag_enabled", True),
            "chat_enabled": member.get("chat_enabled", True),
        })

    return user_usage_list


@router.patch(
    "/users/{user_id}/access",
    response_model=dict,
    dependencies=[Depends(require_permission("usage", "manage"))],
)
async def update_user_access(
    user_id: UUID,
    access_data: UserAccessUpdate,
    current_user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Update user's access to RAG and chat features.

    **Requires:** usage.manage permission

    Body Parameters:
    - **rag_enabled**: Enable/disable RAG (document search) access
    - **chat_enabled**: Enable/disable chat access
    """
    from src.core.database import db

    org_id = UUID(org_context["org_id"])
    current_user_id = UUID(current_user["id"])

    # Build update dict
    update_data = {}
    if access_data.rag_enabled is not None:
        update_data["rag_enabled"] = access_data.rag_enabled
    if access_data.chat_enabled is not None:
        update_data["chat_enabled"] = access_data.chat_enabled

    if not update_data:
        raise HTTPException(
            status_code=400,
            detail="No access settings provided"
        )

    # Update organization member
    result = db.admin.table("organization_members").update(
        update_data
    ).eq("org_id", str(org_id)).eq("user_id", str(user_id)).execute()

    if not result.data:
        raise HTTPException(
            status_code=404,
            detail="User not found in organization"
        )

    # Log the action
    await audit_service.log_action(
        org_id=org_id,
        user_id=current_user_id,
        action="update",
        resource_type="user_access",
        resource_id=user_id,
        details={
            "rag_enabled": access_data.rag_enabled,
            "chat_enabled": access_data.chat_enabled
        },
        severity="info"
    )

    return {
        "message": "User access updated successfully",
        "access": result.data[0]
    }
