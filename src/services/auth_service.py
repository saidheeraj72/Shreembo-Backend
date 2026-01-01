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
        if not user_profile.get("org_id"):
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

    @staticmethod
    async def accept_invitation(
        invite_token: str,
        email: str,
        password: str,
        full_name: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict:
        """
        Accept an invitation and create/link user account.

        Args:
            invite_token: Invitation token
            email: User email (must match invitation)
            password: Password for new account
            full_name: User full name
            ip_address: Client IP
            user_agent: Client user agent

        Returns:
            Dictionary with access_token, refresh_token, and user data
        """
        from src.core.exceptions import NotFoundError, BadRequestError

        # 1. Find invitation
        invitation_response = (
            db.admin.table("organization_invitations")
            .select("*")
            .eq("invite_token", invite_token)
            .eq("status", "pending")
            .gt("expires_at", datetime.utcnow().isoformat())
            .maybe_single()
            .execute()
        )

        if not invitation_response.data:
            raise NotFoundError("Invalid or expired invitation")

        invitation = invitation_response.data
        
        # Verify email matches
        if invitation["email"].lower() != email.lower():
            raise BadRequestError("Email address does not match the invitation.")
            
        # Continue with extracted email from invitation (trusted source)
        email = invitation["email"]
        org_id = invitation["org_id"]

        # 2. Check if user exists
        existing_user_response = (
            db.admin.table("profiles")
            .select("*")
            .eq("email", email)
            .execute()
        )
        
        print(f"DEBUG: existing_user_response: {existing_user_response}")

        if existing_user_response and existing_user_response.data and len(existing_user_response.data) > 0:
            # User exists - link to organization
            user_id = existing_user_response.data[0]["id"]
            
            # If user already has an org, we might want to handle that
            # For now, we'll overwrite or check logic.
            # Assuming user can only be in one org at a time for this model
            
            db.admin.table("profiles").update({
                "org_id": org_id,
                "status": "active", # Ensure active
                "account_type": "organization" # Switch type if personal
            }).eq("id", user_id).execute()
            
            # Add to organization_members
            db.admin.table("organization_members").upsert({
                "org_id": org_id,
                "user_id": user_id,
                "role_id": invitation["role_id"],
                "status": "active",
                "invited_by": invitation["invited_by"],
                "invited_at": invitation["invited_at"],
                "joined_at": datetime.utcnow().isoformat()
            }, on_conflict="org_id, user_id").execute()
            
            # Log in the user to get tokens (re-using login logic)
            # This requires the user to know their existing password?
            # Wait, if they are accepting an invite, they might expect to just 'get in'.
            # But we can't generate a token without their password if using Supabase Auth directly.
            # If the user exists, we probably shouldn't be asking for a password in `accept_invitation`
            # unless we are resetting it.
            # For simplicity, if user exists, we expect them to login normally, 
            # BUT the invite link usually logs you in.
            # If we can't sign them in without password, we should just link them and tell them to login.
            
            # However, the prompt implies a flow where they provide password.
            # If they provide a password and the user exists, we could try to sign in with it.
            
            try:
                 login_result = await AuthService.login(email, password, ip_address, user_agent)
            except AuthenticationError:
                # If password doesn't match, we still linked them, but can't return tokens.
                # We might raise an error saying "User exists, please login"
                raise ConflictError("User already exists. Please login to access your organization.")
                
            # Update invitation status
            db.admin.table("organization_invitations").update({
                "status": "accepted",
                "accepted_at": datetime.utcnow().isoformat(),
                "accepted_by": user_id
            }).eq("id", invitation["id"]).execute()
            
            return login_result

        else:
            # 3. Create new user
            try:
                # Reuse signup logic but with specific org details
                auth_response = db.anon.auth.sign_up({
                    "email": email,
                    "password": password,
                })
            except Exception as e:
                raise ConflictError(f"Failed to create user: {str(e)}")

            user_id = auth_response.user.id

            # Create profile linked to org
            profile_data = {
                "id": user_id,
                "email": email.lower(),
                "full_name": full_name,
                "account_type": "organization",
                "org_id": org_id,
                "status": "active",
                "plan_type": "enterprise", # Inherit from org?
            }

            db.admin.table("profiles").insert(profile_data).execute()

            # Add to organization_members
            db.admin.table("organization_members").insert({
                "org_id": org_id,
                "user_id": user_id,
                "role_id": invitation["role_id"],
                "status": "active",
                "invited_by": invitation["invited_by"],
                "invited_at": invitation["invited_at"],
                "joined_at": datetime.utcnow().isoformat()
            }).execute()

            # Update invitation status
            db.admin.table("organization_invitations").update({
                "status": "accepted",
                "accepted_at": datetime.utcnow().isoformat(),
                "accepted_by": user_id
            }).eq("id", invitation["id"]).execute()
            
            # Log creation
            await audit_service.log(
                org_id=UUID(org_id),
                user_id=UUID(user_id),
                user_email=email,
                user_name=full_name,
                action=AuditAction.CREATE,
                resource_type="user",
                resource_id=UUID(user_id),
                description="User joined via invitation",
                ip_address=ip_address,
                user_agent=user_agent,
            )

            # Return tokens
            # Since we just signed up, we can sign in to get tokens
            return await AuthService.login(email, password, ip_address, user_agent)


# Global auth service instance
auth_service = AuthService()
