"""Auto-split auth service part."""
from datetime import datetime, timedelta
from typing import Optional, Dict
from uuid import UUID
import logging

from src.core.database import db
from src.core.exceptions import AuthenticationError, ConflictError, NotFoundError, ValidationError
from src.audit.service import audit_service
from src.models.audit import AuditAction

logger = logging.getLogger(__name__)


class AuthLoginMixin:
    @staticmethod
    async def login(
        email: str,
        password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict:
        """
        Authenticate user and return tokens.

        Args:
            email: User email
            password: User password
            ip_address: Client IP
            user_agent: Client user agent

        Returns:
            Dictionary with access_token, refresh_token, and user data

        Raises:
            AuthenticationError: If credentials are invalid
        """
        # Use Supabase Auth for authentication
        try:
            auth_response = db.anon.auth.sign_in_with_password({
                "email": email,
                "password": password,
            })
        except Exception as e:
            # Log failed login attempt
            await audit_service.log(
                org_id=None,
                user_id=UUID("00000000-0000-0000-0000-000000000000"),  # Unknown user
                user_email=email,
                action=AuditAction.LOGIN,
                resource_type="auth",
                description="Failed login attempt",
                details={"error": str(e)},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise AuthenticationError("Invalid email or password")

        user_id = auth_response.user.id

        # Get user profile
        profile_response = (
            db.admin.table("profiles")
            .select("*")
            .eq("id", user_id)
            .single()
            .execute()
        )

        if not profile_response.data:
            raise AuthenticationError("User profile not found")

        user_profile = profile_response.data

        # Auto-assign to organization if user has no org but email domain matches
        if not user_profile.get("org_id"):
            email_domain = email.split("@")[1] if "@" in email else None

            if email_domain:
                logger.info("Checking for organization with domain: %s", email_domain)

                # Find organization by domain
                org_response = (
                    db.admin.table("organizations")
                    .select("id, name")
                    .eq("domain", email_domain)
                    .eq("is_active", True)
                    .maybe_single()
                    .execute()
                )

                if org_response and org_response.data:
                    org_id = org_response.data["id"]
                    org_name = org_response.data["name"]

                    logger.info("Found matching organization: %s (ID: %s)", org_name, org_id)
                    logger.info("Auto-assigning user to organization")

                    # Update user profile to link to organization and activate
                    db.admin.table("profiles").update({
                        "org_id": org_id,
                        "status": "active",
                        "last_login_at": datetime.utcnow().isoformat(),
                        "last_active_at": datetime.utcnow().isoformat(),
                    }).eq("id", user_id).execute()

                    # Refresh user profile
                    profile_response = (
                        db.admin.table("profiles")
                        .select("*")
                        .eq("id", user_id)
                        .single()
                        .execute()
                    )
                    user_profile = profile_response.data

                    logger.info("User auto-assigned to organization: %s", org_name)
                else:
                    logger.info("No organization found with domain: %s", email_domain)

        # Check user status
        if user_profile.get("status") != "active":
            raise AuthenticationError(f"Account is {user_profile.get('status')}")

        # Update last login
        db.admin.table("profiles").update({
            "last_login_at": datetime.utcnow().isoformat(),
            "last_active_at": datetime.utcnow().isoformat(),
        }).eq("id", user_id).execute()

        # Log successful login
        await audit_service.log(
            org_id=UUID(user_profile["org_id"]) if user_profile.get("org_id") else None,
            user_id=UUID(user_id),
            user_email=user_profile["email"],
            user_name=user_profile.get("full_name"),
            action=AuditAction.LOGIN,
            resource_type="auth",
            description="User logged in",
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return {
            "access_token": auth_response.session.access_token,
            "refresh_token": auth_response.session.refresh_token,
            "user": user_profile,
        }

