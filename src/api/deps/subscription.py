"""
Dependencies for subscription checking.
"""
from typing import Dict
from fastapi import Depends
from uuid import UUID

from src.core.dependencies import get_current_user_id
from src.core.database import db
from src.access.subscription import subscription_service


async def check_active_subscription(
    user_id: UUID = Depends(get_current_user_id)
) -> Dict:
    """
    Dependency to check if user has an active subscription.

    Args:
        user_id: Current user ID from JWT token

    Returns:
        Subscription information dictionary

    Raises:
        AuthorizationError: If subscription is expired or inactive
    """
    # Get user profile to determine account type and org_id
    profile_response = (
        db.admin.table("profiles")
        .select("account_type, org_id")
        .eq("id", str(user_id))
        .single()
        .execute()
    )

    if not profile_response.data:
        from src.core.exceptions import AuthenticationError
        raise AuthenticationError("User profile not found")

    account_type = profile_response.data.get("account_type")
    org_id = profile_response.data.get("org_id")

    # Check subscription status
    subscription_info = await subscription_service.check_subscription_active(
        user_id=user_id,
        account_type=account_type,
        org_id=UUID(org_id) if org_id else None
    )

    return subscription_info


async def get_subscription_info_dep(
    user_id: UUID = Depends(get_current_user_id)
) -> Dict:
    """
    Dependency to get subscription information without enforcing active status.
    Useful for displaying subscription details in UI.

    Args:
        user_id: Current user ID from JWT token

    Returns:
        Subscription information dictionary
    """
    # Get user profile
    profile_response = (
        db.admin.table("profiles")
        .select("account_type, org_id")
        .eq("id", str(user_id))
        .maybe_single()
        .execute()
    )

    if not profile_response or not profile_response.data:
        return {
            "is_active": False,
            "error": "User profile not found"
        }

    account_type = profile_response.data.get("account_type")
    org_id = profile_response.data.get("org_id")

    # Get subscription info
    subscription_info = await subscription_service.get_subscription_info(
        user_id=user_id,
        account_type=account_type,
        org_id=UUID(org_id) if org_id else None
    )

    return subscription_info
