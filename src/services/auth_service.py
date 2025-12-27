"""
Authentication service for user signup, login, and session management.
"""
from datetime import datetime, timedelta
from typing import Optional, Dict
from uuid import UUID

from src.core.database import db
from src.core.security import create_access_token, create_refresh_token, get_password_hash
from src.core.exceptions import AuthenticationError, ConflictError
from src.services.audit_service import audit_service
from src.models.audit import AuditAction


class AuthService:
    """Service for authentication operations."""

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
        if not user_profile.get("org_id") and user_profile.get("account_type") != "personal":
            email_domain = email.split("@")[1] if "@" in email else None

            if email_domain:
                print(f"🔍 Checking for organization with domain: {email_domain}")

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

                    print(f"✅ Found matching organization: {org_name} (ID: {org_id})")
                    print(f"🔗 Auto-assigning user to organization...")

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

                    print(f"✅ User auto-assigned to organization: {org_name}")
                else:
                    print(f"ℹ️ No organization found with domain: {email_domain}")

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

    @staticmethod
    async def signup(
        email: str,
        password: str,
        full_name: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict:
        """
        Register a new user.

        Args:
            email: User email
            password: User password
            full_name: User full name
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
            "account_type": "personal",
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

        return profile_response.data

    @staticmethod
    async def get_current_user_with_permissions(user_id: UUID, org_id: Optional[UUID]) -> Dict:
        """
        Get user profile with permissions and super admin status.

        Args:
            user_id: User ID
            org_id: Organization ID

        Returns:
            User profile with permissions and super admin status
        """
        from src.services.permission_service import permission_service
        from src.services.super_admin_service import super_admin_service

        # Get user profile
        profile_response = (
            db.admin.table("profiles")
            .select("*")
            .eq("id", str(user_id))
            .single()
            .execute()
        )

        user_data = profile_response.data

        # Check if user is super admin
        is_super_admin = await super_admin_service.verify_super_admin(user_data["email"])
        user_data["is_super_admin"] = is_super_admin

        # Get permissions if user is in an organization
        if org_id:
            permissions = await permission_service.get_user_permissions(user_id, org_id)
            user_data["permissions"] = permissions
        else:
            user_data["permissions"] = {}

        # Super admins get access to super_admin module
        if is_super_admin:
            if "super_admin" not in user_data["permissions"]:
                user_data["permissions"]["super_admin"] = {}
            user_data["permissions"]["super_admin"]["access"] = True

        return user_data


# Global auth service instance
auth_service = AuthService()
