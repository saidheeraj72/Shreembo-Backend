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


class AuthInviteAcceptMixin:
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
            
            # Client keeps using existing tokens; return empty tokens with updated user
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
        
        logger.debug("existing_user_response: %s", existing_user_response)

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

