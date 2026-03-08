"""Auto-split admin service part."""
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import UUID
import secrets
import logging

from src.core.database import db
from src.core.cache import cache
from src.core.exceptions import NotFoundError, ConflictError, AuthorizationError
from src.audit.service import audit_service
from src.email.service import email_service
from src.models.audit import AuditAction

logger = logging.getLogger(__name__)


class AdminInvitationResendMixin:
    @staticmethod
    async def resend_invitation(
        org_id: UUID,
        invitation_id: UUID,
        resent_by: UUID,
    ) -> dict:
        """
        Resend an invitation email.

        Args:
            org_id: Organization UUID
            invitation_id: Invitation UUID
            resent_by: User resending

        Returns:
            Updated invitation
        """
        # Verify invitation exists
        existing = (
            db.admin.table("organization_invitations")
            .select("*")
            .eq("id", str(invitation_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("Invitation not found")

        if existing.data["status"] != "pending":
            raise ConflictError(f"Cannot resend - invitation is {existing.data['status']}")

        # Generate new token and extend expiry
        new_token = secrets.token_urlsafe(32)
        new_expiry = datetime.utcnow() + timedelta(days=7)

        response = (
            db.admin.table("organization_invitations")
            .update({
                "invite_token": new_token,
                "expires_at": new_expiry.isoformat(),
            })
            .eq("id", str(invitation_id))
            .execute()
        )

        # Send invitation email
        try:
            # Fetch details for email
            org_response = db.admin.table("organizations").select("name").eq("id", str(org_id)).single().execute()
            resent_by_response = db.admin.table("profiles").select("full_name").eq("id", str(resent_by)).single().execute()
            
            org_name = org_response.data["name"] if org_response.data else "Organization"
            resent_by_name = resent_by_response.data["full_name"] if resent_by_response.data else "An admin"
            
            # Check if user already has an account on the platform
            user_profile = (
                db.admin.table("profiles")
                .select("id")
                .eq("email", existing.data["email"].lower())
                .maybe_single()
                .execute()
            )
            user_exists = user_profile is not None and user_profile.data is not None
            
            email_service.send_invitation_email(
                to_email=existing.data["email"],
                invite_token=new_token,
                inviter_name=resent_by_name,
                org_name=org_name,
                message="Invitation resent.",
                user_exists=user_exists
            )
        except Exception as e:
            logger.error(f"Failed to resend invitation email: {e}")

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=resent_by,
            action=AuditAction.UPDATE,
            resource_type="invitation",
            resource_id=invitation_id,
            resource_name=existing.data["email"],
            description=f"Resent invitation to: {existing.data['email']}",
        )

        return response.data[0] if response.data else existing.data

