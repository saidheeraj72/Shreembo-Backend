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
    async def verify_invitation(invite_token: str) -> Dict:
        """
        Verify an invitation token and check if user already exists.
        
        This is used by the frontend to determine the invitation flow:
        - If user exists: redirect to login page with token
        - If user is new: show signup form
        
        Args:
            invite_token: Invitation token from URL
            
        Returns:
            Dictionary with invitation details and whether user exists
        """
        from src.core.exceptions import NotFoundError
        
        # Find invitation
        invitation_response = (
            db.admin.table("organization_invitations")
            .select("*, organizations(id, name, logo_url), profiles!organization_invitations_invited_by_fkey(full_name)")
            .eq("invite_token", invite_token)
            .maybe_single()
            .execute()
        )
        
        if not invitation_response.data:
            return {
                "valid": False,
                "user_exists": False,
                "email": "",
                "org_name": "",
                "message": "Invitation not found"
            }
        
        invitation = invitation_response.data
        
        # Check if expired
        from datetime import datetime
        expires_at = datetime.fromisoformat(invitation["expires_at"].replace("Z", "+00:00"))
        if expires_at < datetime.utcnow().replace(tzinfo=expires_at.tzinfo):
            return {
                "valid": False,
                "user_exists": False,
                "email": invitation["email"],
                "org_name": invitation.get("organizations", {}).get("name", ""),
                "message": "Invitation has expired"
            }
        
        # Check if already accepted/cancelled
        if invitation["status"] != "pending":
            return {
                "valid": False,
                "user_exists": False,
                "email": invitation["email"],
                "org_name": invitation.get("organizations", {}).get("name", ""),
                "message": f"Invitation has already been {invitation['status']}"
            }
        
        # Check if user already exists
        existing_user = (
            db.admin.table("profiles")
            .select("id, email, full_name")
            .eq("email", invitation["email"].lower())
            .maybe_single()
            .execute()
        )
        
        user_exists = existing_user.data is not None
        
        org_data = invitation.get("organizations", {}) or {}
        inviter_data = invitation.get("profiles", {}) or {}
        
        return {
            "valid": True,
            "user_exists": user_exists,
            "email": invitation["email"],
            "org_name": org_data.get("name", ""),
            "org_logo": org_data.get("logo_url"),
            "inviter_name": inviter_data.get("full_name"),
            "expires_at": invitation["expires_at"],
            "message": invitation.get("message")
        }

    @staticmethod
    async def accept_invitation(
        invite_token: str,
        email: str,
        password: Optional[str] = None,
        full_name: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        current_user: Optional[Dict] = None,
    ) -> Dict:
        """
        Accept an invitation and create/link user account.

        Args:
            invite_token: Invitation token
            email: User email (must match invitation)
            password: Password for new account (optional if logged in)
            full_name: User full name (optional if logged in)
            ip_address: Client IP
            user_agent: Client user agent
            current_user: Currently logged in user (optional)

        Returns:
            Dictionary with access_token, refresh_token, and user data
        """
        from src.core.exceptions import NotFoundError, BadRequestError, AuthenticationError

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
        invite_email = invitation["email"]
        org_id = invitation["org_id"]

        user_id = None
        
        # Handle Logged-in User Case
        if current_user:
            # Verify logged-in user email matches invite
            if current_user["email"].lower() != invite_email.lower():
                raise BadRequestError("Logged-in user email does not match invitation.")
            
            user_id = current_user["id"]
            
            # Update profile to link to org
            # Note: We update org_id only if not already set, OR we might overwrite if moving?
            # Usually, switching orgs is a context switch, not a profile overwrite.
            # But the 'profiles' table has 'org_id' which implies primary/current org.
            # For this MVP, we set it.
            db.admin.table("profiles").update({
                "org_id": org_id,
                "status": "active", 
                "account_type": "organization"
            }).eq("id", user_id).execute()
            
            # Upsert into organization_members
            db.admin.table("organization_members").upsert({
                "org_id": org_id,
                "user_id": user_id,
                "role_id": invitation["role_id"],
                "status": "active",
                "invited_by": invitation["invited_by"],
                "invited_at": invitation["invited_at"],
                "joined_at": datetime.utcnow().isoformat()
            }, on_conflict="org_id, user_id").execute()
            
            # Mark invitation accepted
            db.admin.table("organization_invitations").update({
                "status": "accepted",
                "accepted_at": datetime.utcnow().isoformat(),
                "accepted_by": user_id
            }).eq("id", invitation["id"]).execute()
            
            # Return current session tokens (we don't have them here, so we might need to regenerate or assume client has them)
            # BUT the return type expects tokens.
            # Since user is already logged in, the client actually has tokens.
            # However, to be consistent, we can generate new tokens or just return dummy/refreshed ones.
            # Ideally, we call login() but we don't have the password.
            # Supabase session management is tricky here if we don't have the session object.
            # We can create a custom token using `create_access_token` if we were managing it fully custom.
            # But we rely on Supabase Auth.
            # Workaround: Return empty tokens and let client keep using existing ones?
            # Or better: The client refetches 'me' after this call.
            # We'll return dummy tokens and the updated user object.
            
            # Refresh user profile
            updated_profile = (
                db.admin.table("profiles")
                .select("*")
                .eq("id", user_id)
                .single()
                .execute()
            )
            
            return {
                "access_token": "", # Client should ignore or reuse existing
                "refresh_token": "",
                "user": updated_profile.data
            }

        # 2. Check if user exists (Not logged in but has account)
        existing_user_response = (
            db.admin.table("profiles")
            .select("*")
            .eq("email", invite_email)
            .execute()
        )
        
        print(f"DEBUG: existing_user_response: {existing_user_response}")

        if existing_user_response and existing_user_response.data and len(existing_user_response.data) > 0:
            # User exists - link to organization
            user_id = existing_user_response.data[0]["id"]
            
            if not password:
                 raise BadRequestError("Password is required to verify identity.")

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
            
            # Login to verify password and get tokens
            try:
                 login_result = await AuthService.login(invite_email, password, ip_address, user_agent)
            except AuthenticationError:
                raise ConflictError("User already exists but password incorrect. Please login first.")
                
            # Update invitation status
            db.admin.table("organization_invitations").update({
                "status": "accepted",
                "accepted_at": datetime.utcnow().isoformat(),
                "accepted_by": user_id
            }).eq("id", invitation["id"]).execute()
            
            return login_result

        else:
            # 3. Create new user
            if not password:
                 raise BadRequestError("Password is required to create account.")
            if not full_name:
                 raise BadRequestError("Full Name is required.")

            try:
                # Reuse signup logic but with specific org details
                auth_response = db.anon.auth.sign_up({
                    "email": invite_email,
                    "password": password,
                })
            except Exception as e:
                raise ConflictError(f"Failed to create user: {str(e)}")

            user_id = auth_response.user.id

            # Create profile linked to org
            profile_data = {
                "id": user_id,
                "email": invite_email.lower(),
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
                user_email=invite_email,
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
            return await AuthService.login(invite_email, password, ip_address, user_agent)

    @staticmethod
    async def get_user_organizations(user_id: UUID) -> list:
        """
        Get all organizations the user is a member of.

        Args:
            user_id: User ID

        Returns:
            List of organizations with membership details
        """
        # Get organization memberships
        memberships_response = (
            db.admin.table("organization_members")
            .select("org_id, status, role_id, joined_at, title, department")
            .eq("user_id", str(user_id))
            .eq("status", "active")
            .execute()
        )

        if not memberships_response.data:
            return []

        # Get organization details for each membership
        organizations = []
        for membership in memberships_response.data:
            org_response = (
                db.admin.table("organizations")
                .select("id, name, slug, logo_url, domain, is_active")
                .eq("id", membership["org_id"])
                .eq("is_active", True)
                .maybe_single()
                .execute()
            )

            if org_response and org_response.data:
                org_data = org_response.data
                org_data["role_id"] = membership["role_id"]
                org_data["joined_at"] = membership["joined_at"]
                org_data["title"] = membership["title"]
                org_data["department"] = membership["department"]
                organizations.append(org_data)

        return organizations

    @staticmethod
    async def switch_organization(user_id: UUID, target_org_id: Optional[UUID]) -> Dict:
        """
        Switch user's active organization context.

        Args:
            user_id: User ID
            target_org_id: Target organization ID (None for personal workspace)

        Returns:
            Updated user data with new org context and permissions
        """
        from src.services.permission_service import permission_service
        from src.services.super_admin_service import super_admin_service
        from src.core.exceptions import AuthorizationError, NotFoundError

        # Get user profile
        profile_response = (
            db.admin.table("profiles")
            .select("*")
            .eq("id", str(user_id))
            .single()
            .execute()
        )

        if not profile_response.data:
            raise NotFoundError("User not found")

        user_profile = profile_response.data

        # If switching to personal workspace (null org)
        if target_org_id is None:
            # Update profile to personal context
            db.admin.table("profiles").update({
                "org_id": None,
                "account_type": "personal"
            }).eq("id", str(user_id)).execute()

            # Refresh profile
            updated_profile = (
                db.admin.table("profiles")
                .select("*")
                .eq("id", str(user_id))
                .single()
                .execute()
            )

            user_data = updated_profile.data
            user_data["permissions"] = {}
            
            # Check super admin status
            is_super_admin = await super_admin_service.verify_super_admin(user_data["email"])
            user_data["is_super_admin"] = is_super_admin
            if is_super_admin:
                user_data["permissions"]["super_admin"] = {"access": True}

            return user_data

        # Verify user is a member of the target organization
        membership_response = (
            db.admin.table("organization_members")
            .select("*")
            .eq("user_id", str(user_id))
            .eq("org_id", str(target_org_id))
            .eq("status", "active")
            .maybe_single()
            .execute()
        )

        if not membership_response.data:
            raise AuthorizationError("You are not a member of this organization")

        # Verify organization is active
        org_response = (
            db.admin.table("organizations")
            .select("id, name, is_active")
            .eq("id", str(target_org_id))
            .eq("is_active", True)
            .maybe_single()
            .execute()
        )

        if not org_response.data:
            raise NotFoundError("Organization not found or inactive")

        # Update profile with new org context
        db.admin.table("profiles").update({
            "org_id": str(target_org_id),
            "account_type": "organization"
        }).eq("id", str(user_id)).execute()

        # Get updated profile with permissions
        return await AuthService.get_current_user_with_permissions(user_id, target_org_id)

    @staticmethod
    async def create_organization(
        user_id: UUID,
        name: str,
        slug: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> Dict:
        """
        Create a new organization with the user as owner.

        Args:
            user_id: User creating the organization
            name: Organization name
            slug: URL-friendly slug (auto-generated if not provided)
            domain: Organization domain (optional)

        Returns:
            Dictionary with organization and updated user data
        """
        from src.core.exceptions import ConflictError
        import re

        # Generate slug if not provided
        if not slug:
            slug = re.sub(r'[^a-z0-9-]', '-', name.lower())
            slug = re.sub(r'-+', '-', slug).strip('-')

        # Check if slug already exists
        existing_slug = (
            db.admin.table("organizations")
            .select("id")
            .eq("slug", slug)
            .maybe_single()
            .execute()
        )

        if existing_slug and existing_slug.data:
            # Add random suffix to make unique
            import random
            import string
            suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
            slug = f"{slug}-{suffix}"

        # Check if domain already exists (if provided)
        if domain:
            existing_domain = (
                db.admin.table("organizations")
                .select("id")
                .eq("domain", domain.lower())
                .maybe_single()
                .execute()
            )

            if existing_domain and existing_domain.data:
                raise ConflictError(f"Domain '{domain}' is already registered to another organization")

        # Create organization
        org_data = {
            "name": name,
            "slug": slug,
            "domain": domain.lower() if domain else None,
            "owner_id": str(user_id),
            "plan_type": "free",
            "subscription_status": "trial",
            "user_limit": 5,
            "storage_limit_gb": 10,
            "is_active": True,
        }

        org_response = db.admin.table("organizations").insert(org_data).execute()

        if not org_response.data:
            raise Exception("Failed to create organization")

        org = org_response.data[0]
        org_id = org["id"]

        # Create default roles for the organization
        default_roles = [
            {
                "org_id": org_id,
                "name": "Owner",
                "slug": "owner",
                "description": "Organization owner with full access",
                "is_system_role": True,
                "is_custom_role": False,
                "priority": 100,
                "color": "#dc2626",
            },
            {
                "org_id": org_id,
                "name": "Admin",
                "slug": "admin",
                "description": "Administrator with management access",
                "is_system_role": True,
                "is_custom_role": False,
                "priority": 90,
                "color": "#ea580c",
            },
            {
                "org_id": org_id,
                "name": "Member",
                "slug": "member",
                "description": "Regular member with standard access",
                "is_system_role": True,
                "is_custom_role": False,
                "priority": 10,
                "color": "#6366f1",
            },
        ]

        for role in default_roles:
            db.admin.table("roles").insert(role).execute()

        # Get the owner role
        owner_role_response = (
            db.admin.table("roles")
            .select("id")
            .eq("org_id", org_id)
            .eq("slug", "owner")
            .single()
            .execute()
        )

        owner_role_id = owner_role_response.data["id"] if owner_role_response.data else None

        # Add user as organization member (owner)
        membership_data = {
            "org_id": org_id,
            "user_id": str(user_id),
            "role_id": owner_role_id,
            "status": "active",
            "joined_at": datetime.utcnow().isoformat(),
        }

        db.admin.table("organization_members").insert(membership_data).execute()

        # Update user profile to point to new org
        db.admin.table("profiles").update({
            "org_id": org_id,
            "account_type": "organization",
        }).eq("id", str(user_id)).execute()

        # Get updated user with permissions
        updated_user = await AuthService.get_current_user_with_permissions(user_id, UUID(org_id))

        # Log the creation
        await audit_service.log(
            org_id=UUID(org_id),
            user_id=user_id,
            user_email=updated_user.get("email"),
            user_name=updated_user.get("full_name"),
            action=AuditAction.CREATE,
            resource_type="organization",
            resource_id=UUID(org_id),
            description=f"Created organization: {name}",
        )

        return {
            "organization": org,
            "user": updated_user,
        }


# Global auth service instance
auth_service = AuthService()

