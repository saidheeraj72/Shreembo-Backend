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


class AuthInviteVerifyMixin:
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

