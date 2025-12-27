"""
Subscription service for checking access based on subscription status.
"""
from datetime import datetime
from typing import Optional, Dict
from uuid import UUID

from src.core.database import db
from src.core.exceptions import AuthorizationError


class SubscriptionService:
    """Service for subscription management and access control."""

    @staticmethod
    async def check_subscription_active(user_id: UUID, account_type: str, org_id: Optional[UUID] = None) -> Dict:
        """
        Check if user's subscription is active and return subscription details.

        Args:
            user_id: User ID
            account_type: Account type ('personal' or 'organization')
            org_id: Organization ID (required for organization accounts)

        Returns:
            Dictionary with subscription status and details

        Raises:
            AuthorizationError: If subscription is expired or inactive
        """
        now = datetime.utcnow()

        if account_type == "personal":
            # Check personal account subscription
            profile_response = (
                db.admin.table("profiles")
                .select("plan_type, subscription_status, trial_ends_at, subscription_ends_at")
                .eq("id", str(user_id))
                .single()
                .execute()
            )

            if not profile_response.data:
                raise AuthorizationError("User profile not found")

            profile = profile_response.data
            subscription_status = profile.get("subscription_status")
            trial_ends = profile.get("trial_ends_at")
            subscription_ends = profile.get("subscription_ends_at")

            # Check if trial has expired
            if subscription_status == "trial" and trial_ends:
                trial_end_date = datetime.fromisoformat(trial_ends.replace('Z', '+00:00'))
                if now > trial_end_date:
                    # Update status to expired
                    db.admin.table("profiles").update({
                        "subscription_status": "expired"
                    }).eq("id", str(user_id)).execute()

                    raise AuthorizationError(
                        "Your trial period has expired. Please upgrade to continue using the service."
                    )

            # Check if subscription has expired
            if subscription_status in ["active", "past_due"] and subscription_ends:
                subscription_end_date = datetime.fromisoformat(subscription_ends.replace('Z', '+00:00'))
                if now > subscription_end_date:
                    # Update status to expired
                    db.admin.table("profiles").update({
                        "subscription_status": "expired"
                    }).eq("id", str(user_id)).execute()

                    raise AuthorizationError(
                        "Your subscription has expired. Please renew to continue using the service."
                    )

            # Check if subscription is in a non-active state
            if subscription_status in ["expired", "cancelled"]:
                raise AuthorizationError(
                    f"Your subscription is {subscription_status}. Please renew to continue."
                )

            return {
                "is_active": True,
                "account_type": "personal",
                "plan_type": profile.get("plan_type"),
                "subscription_status": subscription_status,
                "trial_ends_at": trial_ends,
                "subscription_ends_at": subscription_ends,
            }

        elif account_type == "organization":
            # Check organization subscription
            if not org_id:
                raise AuthorizationError("Organization ID required for organization accounts")

            org_response = (
                db.admin.table("organizations")
                .select("plan_type, subscription_status, trial_ends_at, subscription_ends_at, is_active")
                .eq("id", str(org_id))
                .single()
                .execute()
            )

            if not org_response.data:
                raise AuthorizationError("Organization not found")

            org = org_response.data

            # Check if organization is active
            if not org.get("is_active"):
                raise AuthorizationError("Organization is inactive. Please contact your administrator.")

            subscription_status = org.get("subscription_status")
            trial_ends = org.get("trial_ends_at")
            subscription_ends = org.get("subscription_ends_at")

            # Check if trial has expired
            if subscription_status == "trial" and trial_ends:
                trial_end_date = datetime.fromisoformat(trial_ends.replace('Z', '+00:00'))
                if now > trial_end_date:
                    # Update status to expired
                    db.admin.table("organizations").update({
                        "subscription_status": "expired",
                        "is_active": False
                    }).eq("id", str(org_id)).execute()

                    raise AuthorizationError(
                        "Organization trial period has expired. Please contact your administrator to upgrade."
                    )

            # Check if subscription has expired
            if subscription_status in ["active", "past_due"] and subscription_ends:
                subscription_end_date = datetime.fromisoformat(subscription_ends.replace('Z', '+00:00'))
                if now > subscription_end_date:
                    # Update status to expired
                    db.admin.table("organizations").update({
                        "subscription_status": "expired",
                        "is_active": False
                    }).eq("id", str(org_id)).execute()

                    raise AuthorizationError(
                        "Organization subscription has expired. Please contact your administrator."
                    )

            # Check if subscription is in a non-active state
            if subscription_status in ["expired", "cancelled"]:
                raise AuthorizationError(
                    f"Organization subscription is {subscription_status}. Please contact your administrator."
                )

            return {
                "is_active": True,
                "account_type": "organization",
                "org_id": str(org_id),
                "plan_type": org.get("plan_type"),
                "subscription_status": subscription_status,
                "trial_ends_at": trial_ends,
                "subscription_ends_at": subscription_ends,
            }

        else:
            raise AuthorizationError(f"Invalid account type: {account_type}")

    @staticmethod
    async def get_subscription_info(user_id: UUID, account_type: str, org_id: Optional[UUID] = None) -> Dict:
        """
        Get subscription information without throwing errors.

        Args:
            user_id: User ID
            account_type: Account type
            org_id: Organization ID

        Returns:
            Dictionary with subscription details
        """
        try:
            return await SubscriptionService.check_subscription_active(user_id, account_type, org_id)
        except AuthorizationError as e:
            # Return subscription info even if expired
            now = datetime.utcnow()

            if account_type == "personal":
                profile_response = (
                    db.admin.table("profiles")
                    .select("plan_type, subscription_status, trial_ends_at, subscription_ends_at")
                    .eq("id", str(user_id))
                    .maybe_single()
                    .execute()
                )

                if profile_response and profile_response.data:
                    return {
                        "is_active": False,
                        "account_type": "personal",
                        "plan_type": profile_response.data.get("plan_type"),
                        "subscription_status": profile_response.data.get("subscription_status"),
                        "trial_ends_at": profile_response.data.get("trial_ends_at"),
                        "subscription_ends_at": profile_response.data.get("subscription_ends_at"),
                        "error": str(e),
                    }

            return {
                "is_active": False,
                "error": str(e),
            }


# Singleton instance
subscription_service = SubscriptionService()
