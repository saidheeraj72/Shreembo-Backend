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


class AuthSignupMixin:
    async def signup(
        email: str,
        password: str,
        full_name: str,
        account_type: str = "personal",
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict:
        """
        Register a new user.

        Args:
            email: User email
            password: User password
            full_name: User full name
            account_type: Account type (personal or organization)
            ip_address: Client IP
            user_agent: Client user agent

        Returns:
            Dictionary with user data

        Raises:
            ConflictError: If email already exists
        """
        # Check if email exists
        existing = (
            db.admin.table("profiles")
            .select("id")
            .eq("email", email.lower())
            .maybe_single()
            .execute()
        )

        if existing and existing.data:
            raise ConflictError("Email already registered")

        # Create auth user via Supabase
        try:
            auth_response = db.anon.auth.sign_up({
                "email": email,
                "password": password,
            })
        except Exception as e:
            raise ConflictError(f"Failed to create user: {str(e)}")

        user_id = auth_response.user.id

        # Calculate trial expiry (14 days for personal accounts)
        trial_expiry = datetime.utcnow() + timedelta(days=14)

        # Create profile
        profile_data = {
            "id": user_id,
            "email": email.lower(),
            "full_name": full_name,
            "account_type": account_type,
            "status": "active",
            "plan_type": "free",
            "subscription_status": "trial",
            "trial_ends_at": trial_expiry.isoformat(),
        }

        db.admin.table("profiles").insert(profile_data).execute()

        # Get created profile
        profile_response = (
            db.admin.table("profiles")
            .select("*")
            .eq("id", user_id)
            .single()
            .execute()
        )

        # Log user creation
        await audit_service.log(
            org_id=None,
            user_id=UUID(user_id),
            user_email=email,
            user_name=full_name,
            action=AuditAction.CREATE,
            resource_type="user",
            resource_id=UUID(user_id),
            description="New user signed up",
            ip_address=ip_address,
            user_agent=user_agent,
        )

        # Send welcome email
        from src.email.service import email_service
        email_service.send_welcome_email(email, full_name)

        return profile_response.data

